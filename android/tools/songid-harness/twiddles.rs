use num_complex::Complex;
fn main() {
    for len in [1024usize, 2048] {
        for i in 1..(len >> 2) {
            let a: f32 = -2.0f32 * std::f32::consts::PI / (len as f32) * (i as f32);
            let c: Complex<f32> = Complex::from_polar(1.0, a);
            println!("{} {} {:08x} {:08x}", len, i, c.re.to_bits(), c.im.to_bits());
        }
    }
}
