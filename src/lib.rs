mod backtest_engine;
mod indicators;

use backtest_engine::BacktestEngine;
use indicators::Indicator;
use pyo3::prelude::*;

use crate::indicators::INDICATORS;

#[pymodule]
fn tradekit_rust(py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<BacktestEngine>()?;
    m.add_class::<Indicator>()?;
    m.add_class::<INDICATORS>()?;

    Ok(())
} 
