"""
analyze_windows.py — standalone convenience wrapper.
Prefer: python main.py --analyze [--tz-offset -3] [--top-n 8]
"""
from wifi_optimizer.analyzer import run_analyze

if __name__ == "__main__":
    run_analyze()
