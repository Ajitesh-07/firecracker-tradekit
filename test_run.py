from tradekit_rust import BacktestEngine
import numpy as np
import pandas as pd
import json
import time
    
class MovingAverageCrossover:
    def __init__(self, fast=21, slow=8):
        self.short_window = int(min(fast, slow))
        self.long_window = int(max(fast, slow))    

    def step(self, history_vals, crrPosition):
        if len(history_vals) < self.long_window:
            return 0
        recent_data = history_vals[-self.long_window:]
        
        ma_short = np.mean(recent_data[-self.short_window:])
        ma_long = np.mean(recent_data)
        
        if ma_short > ma_long:
            return 1
        elif ma_short < ma_long:
            return -1
        else:
            return 0

s = MovingAverageCrossover()
st = time.time()
engine = BacktestEngine(s, 30, "historical_data", 0.0)

results = engine.run()
print(f"Done in {time.time() - st}s")
