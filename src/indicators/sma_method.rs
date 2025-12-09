use ndarray::Array1;

pub fn _sma(data: &Array1<f64>, n: usize) -> Array1<f64> {
    assert!(n > 0, "window size must be > 0");
    let len = data.len();
    assert!(n <= len, "window size must be <= data length");

    let slice: Vec<f64> = match data.as_slice_memory_order() {
        Some(s) => s.to_vec(),
        None => data.iter().cloned().collect(),
    };

    let out_len = len - n + 1;
    let mut out = Vec::with_capacity(out_len);

    let mut sum: f64 = 0.0;
    for i in 0..n {
        sum += slice[i];
    }
    out.push(sum / (n as f64));

    for i in n..len {
        sum += slice[i];
        sum -= slice[i - n];
        out.push(sum / (n as f64));
    }

    Array1::from(out)
}


pub fn sma(data: &Array1<f64>, n: usize) -> Array1<f64> {
    let len = data.len();
    if n == 0 || n > len {
        return Array1::from(vec![f64::NAN; len]);
    }

    let compact = _sma(data, n);
    let mut padded = vec![f64::NAN; len];
    let start = n - 1;
    for (i, &v) in compact.as_slice().expect("compact contiguous").iter().enumerate() {
        padded[start + i] = v;
    }
    Array1::from(padded)
}
