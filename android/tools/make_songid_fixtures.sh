#!/bin/sh
# How songid's test fixtures were made (AP5). Linux x86_64, Rust and Python 3.
#
# 1. The desktop's fingerprinter, from PyPI:  pip install shazamio-core==2.0.0
#    and its source: the sdist shazamio_core-2.0.0.tar.gz.
# 2. The harness: shazamio_core's own fingerprinting code built as a program.
#      cp -r shazamio_core-2.0.0/src/fingerprinting tools/songid-harness/src/
#    then add to src/fingerprinting/algorithm.rs (it only exposes what
#    make_signature_from_file already computes):
#      impl SignatureGenerator {
#          pub fn segment_samples(path: &Path, seconds: Option<u32>) -> Result<Vec<i16>, Box<dyn Error>> {
#              let samples = SignatureGenerator::pcm_samples(MonoDecoder::from_file(path)?)?;
#              Ok(SignatureGenerator::middle_segment(&samples, seconds).to_vec())
#          }
#      }
#      (cd tools/songid-harness && cargo build --release)
# 3. For each WAV:
#      python3 tools/songid_desktop_uri.py x.wav           -> x.wav.uri (the desktop's bytes)
#      tools/songid-harness/target/release/harness x.wav x.raw > x.huri
#    x.huri must equal x.wav.uri (the harness is the wheel); x.raw is the
#    16 kHz audio that signature was made from.
# 4. Twiddles.kt: rustc tools/songid-harness/twiddles.rs (with num-complex)
#    prints chfft's cos/sin for n = 1024 and 2048 as float bits.
# 5. logf-glibc.bin: (x, glibc logf(x)) float pairs, little-endian, via ctypes.
set -e
