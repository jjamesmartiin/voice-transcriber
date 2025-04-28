# Voice Transcriber Tests

This directory contains test scripts for the voice transcriber application.

## Test Scripts

1. `voice-transcription-test.py` - A comprehensive test that:
   - Generates test audio using gTTS (Google Text-to-Speech)
   - Tests transcription accuracy against expected output
   - Includes a fallback test with a sine wave audio

## Running the Tests

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
python tests/voice-transcription-test.py
```

## Expected Results

The comprehensive test with gTTS should produce a meaningful test, checking if words from the expected text are found in the transcription. It also includes a fallback test using a sine wave audio file to ensure the pipeline runs without errors. 