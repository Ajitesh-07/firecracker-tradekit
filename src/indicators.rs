pub mod sma_method;
pub mod ewm;

use ndarray::{Array1};
use numpy::PyReadonlyArray1;
use pyo3::prelude::*;
use sma_method::sma;

#[pyclass]
#[derive(Clone, Copy)]
pub enum INDICATORS {
    MEAN,
    STD,
    VARIANCE
}

type ExecFn = fn(&Array1<f64>) -> Option<f64>;

#[pyclass]
pub struct Indicator {
    data: Array1<f64>,
    exec_func: ExecFn
}

#[pymethods]
impl Indicator {
    #[new]
    fn new(data: PyReadonlyArray1<f64>, indicator_type: INDICATORS) -> Self {
        let v= vec![1.0, 2.0, 3.0];

        Indicator { 
            data: Array1::from_vec(v),
            exec_func: (|_price| {
                return Some(1.0);
            })
        }
    }
}
