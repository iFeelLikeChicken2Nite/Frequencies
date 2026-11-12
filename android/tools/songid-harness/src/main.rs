#[allow(dead_code)]
mod fingerprinting {
    #[path = "algorithm.rs"] pub mod algorithm;
    #[path = "decode.rs"] pub mod decode;
    #[path = "hanning.rs"] pub mod hanning;
    #[path = "opus_decoder.rs"] pub mod opus_decoder;
    #[path = "resample.rs"] pub mod resample;
    #[path = "signature_format.rs"] pub mod signature_format;
}
use fingerprinting::algorithm::SignatureGenerator;
use std::io::Write;

// harness <wav> <out.raw> : writes the 16 kHz samples the signature is made from, prints the URI.
fn main() {
    let args: Vec<String> = std::env::args().collect();
    let samples = SignatureGenerator::segment_samples(std::path::Path::new(&args[1]), None).unwrap();
    let mut f = std::fs::File::create(&args[2]).unwrap();
    for s in &samples { f.write_all(&s.to_le_bytes()).unwrap(); }
    let sig = SignatureGenerator::make_signature_from_buffer(samples);
    println!("{}", sig.encode_to_uri().unwrap());
}
