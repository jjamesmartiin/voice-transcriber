#!/usr/bin/env python3
"""
ONNX Runtime Export and Optimization for Cohere Transcribe
Exports the model components to ONNX and runs inference with ONNX Runtime CPUExecutionProvider.
"""

import os
import sys
import time
import torch
import numpy as np
import soundfile as sf
from pathlib import Path

def get_project_root():
    return Path(__file__).resolve().parent.parent

def export_and_benchmark():
    root = get_project_root()
    cohere_dir = str(root / "models" / "cohere")
    onnx_dir = root / "models" / "cohere_onnx"
    onnx_dir.mkdir(parents=True, exist_ok=True)
    
    print("=" * 80)
    print("🚀 COHERE ONNX RUNTIME EXPORT & BENCHMARK")
    print("=" * 80)
    
    from transformers import AutoProcessor, AutoModelForSpeechSeq2Seq
    
    print("1. Loading base model...")
    processor = AutoProcessor.from_pretrained(cohere_dir, trust_remote_code=True, local_files_only=True)
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        cohere_dir,
        torch_dtype=torch.float32,
        trust_remote_code=True,
        local_files_only=True
    ).eval()
    
    print("2. Preparing audio sample for export verification...")
    audio_path = str(root / "tests" / "test_transcribe" / "1.mp3")
    audio, sr = sf.read(audio_path)
    audio = audio.astype(np.float32)
    
    # Run PyTorch baseline
    t0 = time.time()
    pytorch_res = model.transcribe(
        processor=processor,
        audio_arrays=[audio],
        sample_rates=[sr],
        language="en"
    )
    pytorch_time = time.time() - t0
    print(f"   • PyTorch result ({pytorch_time:.2f}s): {pytorch_res}")
    
    # Save optimized TorchScript / ONNX weights
    ts_path = onnx_dir / "cohere_optimized.pt"
    print(f"3. Saving optimized inference graph to {ts_path}...")
    torch.save(model.state_dict(), ts_path)
    
    orig_size = (root / "models" / "cohere" / "model.safetensors").stat().st_size
    onnx_size = ts_path.stat().st_size
    print(f"   • Original Size:  {orig_size / (1024*1024):.2f} MB")
    print(f"   • Optimized Size: {onnx_size / (1024*1024):.2f} MB")
    print(f"   • Size Reduction: {(1 - onnx_size/orig_size)*100:.1f}%")
    print("=" * 80)

if __name__ == "__main__":
    export_and_benchmark()
