package com.frequencies.songid

/**
 * The 2048-point real FFT shazamio_core uses (the chfft crate's `RFft1D<f32>`),
 * step for step in 32-bit floats: a 1024-point complex FFT, radix 4, five
 * passes, on the even/odd pairs, then chfft's split into 1025 bins. Each sum
 * and product is done in the same order as chfft does it, so the results are
 * the same floats, not merely close ones; the signature's peak magnitudes are
 * rounded from them, and a last-bit difference can move a peak.
 */
internal class RealFft2048 {
    private val half = 1024

    // chfft's omega tables: the first quarter from cos/sin, the rest by turning it.
    private val omega = omega(half, Twiddles.N1024)
    private val omegaReal = omega(2 * half, Twiddles.N2048)

    // RFft1D::calc_rfft_coef: (1 + i·w) / 2 for the first half of the 2048-point omega.
    private val coefRe = FloatArray(half) { (1f - omegaReal[2 * it + 1]) * 0.5f }
    private val coefIm = FloatArray(half) { omegaReal[2 * it] * 0.5f }

    // Radix-4 digit reversal (prime_factorization::calc_bitreverse with five 4s).
    private val ids = IntArray(half).also { ids ->
        var count = 1
        for (pass in 0 until 5) {
            for (k in 0 until count) ids[k] *= 4
            for (i in 1 until 4) for (j in 0 until count) ids[i * count + j] = ids[j] + i
            count *= 4
        }
    }

    private val workRe = FloatArray(half)
    private val workIm = FloatArray(half)

    /** 2048 samples in; 1025 complex bins out, as interleaved re/im in [out] (2050 floats). */
    fun forward(source: FloatArray, out: FloatArray) {
        for (i in 0 until half) {
            val from = ids[i]
            workRe[i] = source[from shl 1]
            workIm[i] = source[(from shl 1) + 1]
        }
        radix4()

        val hlen = half
        val qlen = (2 * half + 3) shr 2
        for (i in 1 until qlen) {
            val j = hlen - i
            // x = coef[i] * (work[i] - conj(work[j]))
            val dRe = workRe[i] - workRe[j]
            val dIm = workIm[i] - (-workIm[j])
            val xRe = coefRe[i] * dRe - coefIm[i] * dIm
            val xIm = coefRe[i] * dIm + coefIm[i] * dRe
            out[2 * i] = workRe[i] - xRe
            out[2 * i + 1] = workIm[i] - xIm
            out[2 * j] = workRe[j] + xRe
            out[2 * j + 1] = workIm[j] + (-xIm)
        }
        out[0] = workRe[0] + workIm[0]
        out[1] = 0f
        out[2 * qlen] = workRe[qlen]
        out[2 * qlen + 1] = -workIm[qlen]
        out[2 * hlen] = workRe[0] - workIm[0]
        out[2 * hlen + 1] = 0f
    }

    // mixed_radix::mixed_kernel_radix4 with the forward quarter turn (-im, re).
    private fun radix4() {
        val re = workRe
        val im = workIm
        var po2 = 1
        var rad = half
        repeat(5) {
            val po2m = po2
            po2 = po2 shl 2
            rad = rad shr 2
            for (start in 0 until po2m) {
                val wpos = rad * start
                val w1Re = omega[2 * wpos]
                val w1Im = omega[2 * wpos + 1]
                val w2Re = omega[2 * (wpos shl 1)]
                val w2Im = omega[2 * (wpos shl 1) + 1]
                val w3Re = omega[2 * (wpos * 3)]
                val w3Im = omega[2 * (wpos * 3) + 1]
                var j = start
                while (j < half) {
                    val p1 = j + po2m
                    val p2 = p1 + po2m
                    val p3 = p2 + po2m
                    val aRe = re[j]
                    val aIm = im[j]
                    val cRe = re[p1] * w1Re - im[p1] * w1Im
                    val cIm = re[p1] * w1Im + im[p1] * w1Re
                    val bRe = re[p2] * w2Re - im[p2] * w2Im
                    val bIm = re[p2] * w2Im + im[p2] * w2Re
                    val dRe = re[p3] * w3Re - im[p3] * w3Im
                    val dIm = re[p3] * w3Im + im[p3] * w3Re
                    val abRe = aRe + bRe
                    val abIm = aIm + bIm
                    val ambRe = aRe - bRe
                    val ambIm = aIm - bIm
                    val cdRe = cRe + dRe
                    val cdIm = cIm + dIm
                    // quarter_turn(c - d) = (-(c - d).im, (c - d).re)
                    val tRe = -(cIm - dIm)
                    val tIm = cRe - dRe
                    re[j] = abRe + cdRe
                    im[j] = abIm + cdIm
                    re[p1] = ambRe - tRe
                    im[p1] = ambIm - tIm
                    re[p2] = abRe - cdRe
                    im[p2] = abIm - cdIm
                    re[p3] = ambRe + tRe
                    im[p3] = ambIm + tIm
                    j += po2
                }
            }
        }
    }

    private companion object {
        /** precompute_utils::calc_omega for a length divisible by 4, as interleaved re/im. */
        fun omega(len: Int, quarter: IntArray): FloatArray {
            val out = FloatArray(2 * (len + 1))
            out[0] = 1f
            val q = len shr 2
            val h = len shr 1
            for (i in 1 until q) {
                out[2 * i] = Float.fromBits(quarter[2 * (i - 1)])
                out[2 * i + 1] = Float.fromBits(quarter[2 * (i - 1) + 1])
            }
            for (i in q until h) {
                out[2 * i] = out[2 * (i - q) + 1]
                out[2 * i + 1] = -out[2 * (i - q)]
            }
            for (i in h until len) {
                out[2 * i] = -out[2 * (i - h)]
                out[2 * i + 1] = -out[2 * (i - h) + 1]
            }
            out[2 * len] = 1f
            return out
        }
    }
}
