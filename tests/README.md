# Voice Transcriber Tests

This directory contains test scripts for the voice transcriber application.

## Test Scripts

1. `test1.py` - A comprehensive test that:
   - Generates test audio using gTTS (Google Text-to-Speech)
   - Tests transcription accuracy against expected output
   - Includes a fallback test with a sine wave audio

2. `test1_simple.py` - A simpler test that:
   - Only uses standard library dependencies
   - Creates a simple sine wave audio file
   - Tests that the transcription pipeline runs without errors
   - Also tests with the existing output.wav file if available

## Running the Tests

### Simple Test (No Additional Dependencies)

```bash
# From the project root directory
python tests/test1_simple.py
```

### Comprehensive Test

First, make sure you have the required dependencies:

#### Option 1: Using the Nix Shell

The main `shell.nix` has been updated to include test dependencies. Simply reload your nix-shell:

```bash
# Exit your current nix-shell if you're in one
exit

# Reload with the updated dependencies
nix-shell
```

#### Option 2: Installing Dependencies Manually

```bash
# Install required packages
pip install -r tests/requirements.txt
```

Then run the test:

```bash
# From the project root directory
python tests/test1.py
```

## Expected Results

- The simple test is expected to run without errors but may not produce meaningful transcription (since it uses a sine wave).
- The comprehensive test with gTTS should produce a more meaningful test, checking if words from the expected text are found in the transcription. 