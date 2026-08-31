#!/usr/bin/env python3
"""
Autonomous Optimization Loop Controller for Voice Transcriber on NixOS.
Runs benchmark iterations in isolated nix develop environment with hardware mutex protection.
Keeps winning commits (faster + 100% tests pass) and reverts regressions.
"""

import os
import sys
import time
import json
import argparse
import subprocess

def get_benchmark_result(agent_tag="Main-Agent"):
    """Runs benchmark.py inside nix shell and parses JSON payload."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    cmd = ["nix", "develop", "--command", "python3", "scripts/benchmark.py", "--agent-tag", agent_tag]
    
    res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
    stdout_lines = [line.strip() for line in res.stdout.strip().split("\n") if line.strip().startswith("{")]
    
    if not stdout_lines:
        return {
            "agent": agent_tag,
            "passed": False,
            "reason": f"No valid JSON output. stderr: {res.stderr[-500:]}",
            "latency_ms": 999999.0
        }
        
    try:
        return json.loads(stdout_lines[-1])
    except Exception as e:
        return {
            "agent": agent_tag,
            "passed": False,
            "reason": f"JSON parse error: {e}",
            "latency_ms": 999999.0
        }

def prompt_optimization_agent(iteration: int, current_latency: float, agent_tag="Main-Agent"):
    prompt = (
        f"[{agent_tag} - Iteration {iteration}] Current Best Latency: {current_latency:.3f} ms.\n"
        "Analyze src/ (post_processor.py, t2.py, micro_batcher.py, transcribe_cohere.py).\n"
        "Propose a concrete performance optimization (e.g. precompiled regex, vectorized operations, "
        "PyTorch thread tuning, JIT compilation, caching).\n"
        "Apply the code changes directly to src/."
    )
    print(f"\n=======================================================")
    print(f"  [{agent_tag}] OPTIMIZATION ITERATION {iteration}")
    print(f"  Target Latency to Beat: {current_latency:.3f} ms")
    print(f"=======================================================")
    print(prompt)

def main():
    parser = argparse.ArgumentParser(description="Auto Optimization Loop Controller")
    parser.add_argument("--iterations", type=int, default=20, help="Max optimization iterations")
    parser.add_argument("--agent-tag", type=str, default="Main-Agent", help="Tag identifying this agent worker")
    parser.add_argument("--dry-run", action="store_true", help="Run benchmark once and exit")
    args = parser.parse_args()

    print(f"⚡ [{args.agent-tag}] Establishing initial performance baseline in Nix environment...")
    baseline = get_benchmark_result(args.agent_tag)
    
    if not baseline["passed"]:
        print(f"❌ [{args.agent-tag}] Initial baseline failed test suite! Reason: {baseline.get('reason')}")
        sys.exit(1)
        
    best_latency = baseline["latency_ms"]
    print(f"\n✅ [{args.agent-tag}] Initial Baseline Established:")
    print(f"   Agent ID      : {baseline.get('agent')}")
    print(f"   Total Latency : {best_latency:.3f} ms")
    print(f"   Post Processor: {baseline.get('post_processor_ms'):.3f} ms")
    print(f"   Buffer Proc   : {baseline.get('buffer_proc_ms'):.3f} ms")

    if args.dry_run:
        print(f"\n[{args.agent-tag} Dry Run Complete] Baseline successfully measured.")
        sys.exit(0)

    for i in range(1, args.iterations + 1):
        prompt_optimization_agent(i, best_latency, args.agent_tag)
        
        input(f"\n[{args.agent-tag}] Press Enter after code change is applied to run benchmark (or Ctrl+C to stop)... ")
        
        print(f"\n[{args.agent-tag}] Running verification benchmark in Nix shell (Mutex locked)...")
        res = get_benchmark_result(args.agent_tag)
        
        if res["passed"] and res["latency_ms"] < best_latency:
            speedup = best_latency / res["latency_ms"]
            diff_ms = best_latency - res["latency_ms"]
            print(f"\n🚀 [{args.agent-tag}] WINNING OPTIMIZATION DETECTED!")
            print(f"   Speedup : {speedup:.2f}x")
            print(f"   Latency : {best_latency:.3f} ms -> {res['latency_ms']:.3f} ms (saved {diff_ms:.3f} ms)")
            
            best_latency = res["latency_ms"]
            subprocess.run(["git", "commit", "-am", f"perf({args.agent-tag}): speedup {speedup:.2f}x ({res['latency_ms']:.3f}ms)"])
        else:
            reason = "Tests failed" if not res["passed"] else f"Slower or equal latency ({res['latency_ms']:.3f} ms >= {best_latency:.3f} ms)"
            print(f"\n❌ [{args.agent-tag}] REJECTED CHANGE: {reason}")
            print(f"   [{args.agent-tag}] Reverting working tree to last clean commit...")
            subprocess.run(["git", "reset", "--hard", "HEAD"])

if __name__ == "__main__":
    main()
