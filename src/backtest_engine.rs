use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use numpy::PyArray1; // Ensure you have "numpy" in your Cargo.toml features
use std::io::{BufReader, BufRead};
use std::fs::File;
use glob::glob;
use serde::{Serialize, Deserialize};
use std::path::Path;

const INITIAL_CAPITAL_PER_STOCK: f64 = 10000.0;
const TRADING_DAYS_PER_YEAR: f64 = 252.0;

#[derive(Debug, Clone, Serialize, Deserialize)]
struct StockMetric {
    ticker: String,
    final_balance: f64,
    trades: i32,
    wins: i32,
    roi_pct: f64,
    buy_and_hold_pct: f64,
    alpha_pct: f64,
    max_drawdown_pct: f64,
    sharpe: f64,
    n_periods: usize,
}

#[pyclass]
pub struct BacktestEngine {
    strategy: PyObject,
    history_size: usize,
    data_folder: String,
    risk_free_rate_annual: f64,
}

#[pymethods]
impl BacktestEngine {
    #[new]
    fn new(strategy: PyObject, history_size: usize, data_folder: String, risk_free_rate_annual: Option<f64>) -> Self {
        BacktestEngine { 
            strategy, 
            history_size, 
            data_folder, 
            risk_free_rate_annual: risk_free_rate_annual.unwrap_or(0.0),
        }
    }

    /// Run backtest. Returns full details in memory (as dict of numpy arrays) instead of writing files.
    fn run(&self, py: Python<'_>) -> PyResult<PyObject> {
        let pattern = format!("{}/*_meso.csv", self.data_folder);
        let paths: Vec<_> = glob(&pattern)
            .expect("Failed to read glob pattern")
            .filter_map(Result::ok)
            .collect();

        let mut metrics_vec: Vec<StockMetric> = Vec::with_capacity(paths.len());
        let py_metrics_list = PyList::empty(py);
        
        // This dictionary will hold { "TICKER": { "dates": [], "closes": np.array, ... } }
        let py_details_map = PyDict::new(py); 

        for path in paths {
            let file_path = path.to_str().unwrap();
            let ticker = path.file_stem().unwrap().to_str().unwrap().replace("_meso", "");

            let price_data = match load_date_and_prices(file_path) {
                Ok(p) => p,
                Err(e) => {
                    eprintln!("Skipping {} because of read error: {}", file_path, e);
                    continue;
                }
            };

            if price_data.len() <= self.history_size + 1 {
                continue;
            }

            // --- Simulation State ---
            let mut balance = INITIAL_CAPITAL_PER_STOCK;
            let mut shares = 0.0;
            let mut in_position = false;
            let mut trades = 0;
            let mut wins = 0;
            let mut entry_price = 0.0;

            // Arrays for calculations
            let mut portfolio_values: Vec<f64> = Vec::with_capacity(price_data.len() - self.history_size);
            let bh_start_price = price_data[self.history_size].1;
            let bh_shares = INITIAL_CAPITAL_PER_STOCK / bh_start_price;
            let mut bh_values: Vec<f64> = Vec::with_capacity(price_data.len() - self.history_size);

            // Vectors to return to Python
            let mut dates: Vec<String> = Vec::with_capacity(price_data.len() - self.history_size);
            let mut closes: Vec<f64> = Vec::with_capacity(price_data.len() - self.history_size);
            let mut signals: Vec<i32> = Vec::with_capacity(price_data.len() - self.history_size);
            let mut balance_history: Vec<f64> = Vec::with_capacity(price_data.len() - self.history_size);
            
            // Indices (usize), typically converted to lists or arrays
            let mut buy_indices: Vec<usize> = Vec::new();
            let mut sell_win_indices: Vec<usize> = Vec::new();
            let mut sell_loss_indices: Vec<usize> = Vec::new();

            for i in self.history_size..price_data.len() {
                let (ref date, current_price) = price_data[i];

                // Prepare history slice for Python Strategy
                let history_slice: Vec<f64> = price_data[i - self.history_size..i].iter().map(|(_d,p)| *p).collect();
                let py_history = PyArray1::from_slice(py, &history_slice);
                let crr_pos_int = if in_position { 1 } else { 0 };

                // Call Strategy
                let signal: i32 = match self.strategy.call_method1(py, "step", (py_history, crr_pos_int)) {
                    Ok(obj) => obj.extract(py).unwrap_or(0),
                    Err(e) => {
                        eprintln!("Error calling strategy.step for {} at index {}: {}", ticker, i, e);
                        0
                    }
                };

                // Apply Logic
                if in_position {
                    if signal == -1 {
                        let revenue = shares * current_price;
                        let profit = revenue - (shares * entry_price);
                        if profit > 0.0 { wins += 1; sell_win_indices.push(i - self.history_size); }
                        else { sell_loss_indices.push(i - self.history_size); }

                        balance = revenue;
                        in_position = false;
                        shares = 0.0;
                        trades += 1;
                    }
                } else {
                    if signal == 1 {
                        in_position = true;
                        entry_price = current_price;
                        shares = if current_price > 0.0 { balance / current_price } else { 0.0 };
                        buy_indices.push(i - self.history_size);
                    }
                }

                // Record Data
                signals.push(signal);
                dates.push(date.clone());
                closes.push(current_price);

                let current_value = if in_position { shares * current_price } else { balance };
                portfolio_values.push(current_value);
                balance_history.push(current_value);

                bh_values.push(bh_shares * current_price);
            }

            // --- Calc Metrics (Same as before) ---
            let final_balance = *portfolio_values.last().unwrap_or(&balance);
            let roi_pct = ((final_balance - INITIAL_CAPITAL_PER_STOCK) / INITIAL_CAPITAL_PER_STOCK) * 100.0;

            let buy_and_hold_pct = if bh_values.len() > 0 {
                let first = bh_values.first().unwrap();
                let last = bh_values.last().unwrap();
                ((last / first) - 1.0) * 100.0
            } else { 0.0 };

            let strategy_returns = pct_changes(&portfolio_values);
            let annualized_return = if portfolio_values.len() > 0 {
                let n_days = portfolio_values.len() as f64;
                (portfolio_values.last().unwrap() / portfolio_values.first().unwrap()).powf(TRADING_DAYS_PER_YEAR / n_days) - 1.0
            } else { 0.0 };

            let std_daily = std_sample(&strategy_returns);
            let annualized_vol = std_daily * TRADING_DAYS_PER_YEAR.sqrt();
            let sharpe = if annualized_vol > 0.0 {
                (annualized_return - self.risk_free_rate_annual) / annualized_vol
            } else { 0.0 };

            let max_dd = max_drawdown(&portfolio_values);
            let alpha = roi_pct - buy_and_hold_pct;

            let metric = StockMetric {
                ticker: ticker.clone(),
                final_balance,
                trades,
                wins,
                roi_pct,
                buy_and_hold_pct,
                alpha_pct: alpha,
                max_drawdown_pct: max_dd * 100.0,
                sharpe,
                n_periods: portfolio_values.len(),
            };

            // --- BUILD PYTHON RETURN OBJECT FOR THIS STOCK ---
            let stock_detail = PyDict::new(py);
            
            // Convert Strings to Python List
            stock_detail.set_item("dates", dates)?;
            
            // Convert numerical Vecs to NumPy Arrays (Zero-copy if possible, otherwise efficient copy)
            stock_detail.set_item("closes", PyArray1::from_vec(py, closes))?;
            stock_detail.set_item("signals", PyArray1::from_vec(py, signals))?;
            stock_detail.set_item("balance_history", PyArray1::from_vec(py, balance_history))?;
            
            // Indices
            stock_detail.set_item("buy_indices", PyArray1::from_vec(py, buy_indices))?;
            stock_detail.set_item("sell_win_indices", PyArray1::from_vec(py, sell_win_indices))?;
            stock_detail.set_item("sell_loss_indices", PyArray1::from_vec(py, sell_loss_indices))?;

            // Add metric summary to details as well for convenience
            let py_metric_dict = PyDict::new(py);
            py_metric_dict.set_item("roi_pct", metric.roi_pct)?;
            py_metric_dict.set_item("sharpe", metric.sharpe)?;
            py_metric_dict.set_item("trades", metric.trades)?;
            stock_detail.set_item("metrics", py_metric_dict)?;

            // Store in main details map
            py_details_map.set_item(ticker.clone(), stock_detail)?;

            // --- Store Summary Metrics for Aggregate Calculation ---
            metrics_vec.push(metric.clone());

            // Add to summary list
            let py_metric = PyDict::new(py);
            py_metric.set_item("ticker", metric.ticker.clone())?;
            py_metric.set_item("final_balance", metric.final_balance)?;
            py_metric.set_item("trades", metric.trades)?;
            py_metric.set_item("wins", metric.wins)?;
            py_metric.set_item("roi_pct", metric.roi_pct)?;
            py_metric.set_item("sharpe", metric.sharpe)?;
            py_metrics_list.append(py_metric)?;
        }

        // --- Calculate Portfolio Aggregates (Unchanged Logic) ---
        let mut total_final_balance = 0.0;
        let mut total_initial_balance = 0.0;
        let mut total_trades = 0;
        let mut total_wins = 0;
        let mut sum_alpha_pct = 0.0;
        let mut count_roi_positive: i32 = 0;
        let mut avg_sharpe: f64 = 0.0;

        for r in &metrics_vec {
            total_initial_balance += INITIAL_CAPITAL_PER_STOCK;
            total_final_balance += r.final_balance;
            total_trades += r.trades;
            total_wins += r.wins;
            avg_sharpe += r.sharpe;
            sum_alpha_pct += r.alpha_pct;
            if r.roi_pct > 0.0 { count_roi_positive += 1; }
        }

        let nstocks = metrics_vec.len() as f64;
        if nstocks > 0.0 { avg_sharpe /= nstocks; }

        let portfolio_roi = if total_initial_balance > 0.0 {
            ((total_final_balance - total_initial_balance) / total_initial_balance) * 100.0
        } else { 0.0 };

        let win_rate = if total_trades > 0 { (total_wins as f64 / total_trades as f64) * 100.0 } else { 0.0 };
        let avg_alpha_pct = if nstocks > 0.0 { sum_alpha_pct / nstocks } else { 0.0 };

        let py_summary = PyDict::new(py);
        py_summary.set_item("stocks_processed", metrics_vec.len())?;
        py_summary.set_item("total_roi_pct", portfolio_roi)?;
        py_summary.set_item("total_trades", total_trades)?;
        py_summary.set_item("win_rate_pct", win_rate)?;
        py_summary.set_item("final_capital", total_final_balance)?;
        py_summary.set_item("average_alpha_pct", avg_alpha_pct)?;
        py_summary.set_item("average_sharpe", avg_sharpe)?;

        // --- Final Return ---
        let py_out = PyDict::new(py);
        py_out.set_item("metrics", py_metrics_list)?;
        py_out.set_item("portfolio_summary", py_summary)?;
        
        // This is the new part: returning the huge data structure instead of file paths
        py_out.set_item("details", py_details_map)?; 

        Ok(py_out.to_object(py))
    }
}

// ----------------- Helper functions (Unchanged) -----------------
fn load_date_and_prices(path: &str) -> Result<Vec<(String, f64)>, std::io::Error> {
    let file = File::open(path)?;
    let reader = BufReader::new(file);
    let mut rows = Vec::new();

    for (index, line) in reader.lines().enumerate() {
        if let Ok(l) = line {
            if index == 0 { continue; } // skip header
            let parts: Vec<&str> = l.split(',').collect();
            if parts.len() > 4 {
                let date = parts[0].trim().to_string();
                if let Ok(p) = parts[4].trim().parse::<f64>() {
                    rows.push((date, p));
                }
            }
        }
    }
    Ok(rows)
}

fn pct_changes(series: &Vec<f64>) -> Vec<f64> {
    if series.len() < 2 { return Vec::new(); }
    let mut res = Vec::with_capacity(series.len() - 1);
    for i in 1..series.len() {
        let prev = series[i-1];
        if prev.abs() < f64::EPSILON { res.push(0.0); }
        else { res.push((series[i] / prev) - 1.0); }
    }
    res
}

fn mean(x: &Vec<f64>) -> f64 {
    if x.is_empty() { return 0.0; }
    x.iter().sum::<f64>() / (x.len() as f64)
}

fn var_sample(x: &Vec<f64>) -> f64 {
    let n = x.len();
    if n < 2 { return 0.0; }
    let m = mean(x);
    x.iter().map(|v| (v - m).powi(2)).sum::<f64>() / ((n - 1) as f64)
}

fn std_sample(x: &Vec<f64>) -> f64 {
    var_sample(x).sqrt()
}

fn max_drawdown(series: &Vec<f64>) -> f64 {
    if series.is_empty() { return 0.0; }
    let mut peak = series[0];
    let mut max_dd = 0.0;
    for &v in series {
        if v > peak { peak = v; }
        let dd = if peak > 0.0 { (peak - v) / peak } else { 0.0 };
        if dd > max_dd { max_dd = dd; }
    }
    max_dd
}