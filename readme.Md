## tradekit-rs

A fast backtesting engine written in Rust, with a Python API built using PyO3.
This allows users to write simple python classes as Strategies and backtest them using the rust engine.

## Development

First generate a python virtual env in the dir and install dependencies
```
python -m venv my_project_env 
pip install maturin numpy pandas matplotlib
```

Then switch to the virtual env and run maturin develop

```
source .venv/bin/activate
maturin develop
```


That’s it — after this, you can import and use it in Python:

```
import tradekit_rust
```

## Notes

Re-run maturin develop after editing Rust code.

Python dependencies are already set up in .venv.

Use --release with maturin develop for optimized builds.
