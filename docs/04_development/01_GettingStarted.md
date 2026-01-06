# Getting Started for Developers

Developer onboarding guide for the Binance Trend Bot codebase.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Running the Bot](#running-the-bot)
- [Running Tests](#running-tests)
- [Code Style](#code-style)
- [Common Development Tasks](#common-development-tasks)
- [Debugging](#debugging)

---

## Prerequisites

### Required

- **Python 3.10+** (3.11 or 3.12 recommended)
- **Git** for version control
- **pip** or **pipx** for package management

### Recommended

- **Visual Studio Code** or **PyCharm** for IDE
- **Docker** (optional, for isolated testing)
- **jq** for JSON parsing in terminal

---

## Development Setup

### 1. Clone Repository

```bash
git clone <repository-url>
cd binance_trading
```

### 2. Create Virtual Environment

```bash
# Create venv
python -m venv .venv

# Activate (Linux/macOS)
source .venv/bin/activate

# Activate (Windows)
.venv\Scripts\activate
```

### 3. Install Dependencies

```bash
# Install base + dev dependencies
pip install -e ".[dev]"

# Install LLM support (optional)
pip install -e ".[llm]"
```

### 4. Configure Environment

```bash
# Copy example environment file
cp .env.example .env

# Edit with your API keys (optional for paper mode)
# BINANCE_TESTNET_FUTURE_API_KEY=...
# BINANCE_TESTNET_FUTURE_SECRET_KEY=...
```

### 5. Verify Installation

```bash
# Run tests
pytest tests/ -v

# Check imports
python -c "from src.config.settings import load_settings; print('OK')"

# Run in paper mode
bot
```

---

## Project Structure

```
binance_trading/
├── src/                      # Application source code
│   ├── main.py               # Entry point
│   ├── models.py             # Core data models
│   ├── config/               # Configuration
│   │   └── settings.py       # Pydantic settings
│   ├── strategy/             # Signal generation
│   │   ├── signals.py        # SignalGenerator
│   │   ├── scoring.py        # ScoringEngine
│   │   ├── regime.py         # RegimeClassifier
│   │   ├── universe.py       # UniverseSelector
│   │   ├── portfolio.py      # PortfolioSelector
│   │   └── package.py        # Strategy specs
│   ├── risk/                 # Risk management
│   │   ├── engine.py         # RiskEngine
│   │   └── sizing.py         # Position sizing
│   ├── execution/            # Order execution
│   │   ├── engine.py         # ExecutionEngine
│   │   ├── paper_simulator.py
│   │   ├── state_store.py
│   │   └── user_stream.py
│   ├── ledger/               # Event sourcing
│   │   ├── events.py         # Event types
│   │   ├── bus.py            # EventBus
│   │   ├── store.py          # EventLedger
│   │   └── state.py          # StateManager
│   ├── connectors/           # External APIs
│   │   ├── rest_client.py    # Binance REST
│   │   ├── ws_client.py      # WebSocket
│   │   ├── news.py           # News ingestion
│   │   └── news_classifier.py
│   ├── features/             # Indicators
│   ├── monitoring/           # Observability
│   ├── backtester/           # Backtesting
│   ├── data/                 # Data models
│   ├── api/                  # Operator API
│   ├── tools/                # CLI tools
│   └── utils/                # Helpers
├── strategies/               # Strategy specifications
│   └── trend_following_v1/
│       └── strategy.md
├── tests/                    # Test suite
├── data/                     # Runtime data (gitignored)
├── logs/                     # Log files (gitignored)
├── config.yaml               # Configuration
├── pyproject.toml            # Package metadata
└── README.md                 # Project overview
```

---

## Running the Bot

### Paper Mode (Default)

```bash
# Uses config.yaml defaults
bot
```

### With Custom Config

```bash
# Environment variable
CONFIG_PATH=custom_config.yaml bot

# Or modify config.yaml directly
```

### Check Status

```bash
# Health check
curl http://localhost:8000/health

# View state
curl http://localhost:8000/state | jq

# View metrics
curl http://localhost:9090/metrics | grep loop_last_tick
```

---

## Running Tests

### Run All Tests

```bash
pytest tests/ -v
```

### Run Specific Tests

```bash
# Single file
pytest tests/test_backtester_funding.py -v

# Single test
pytest tests/test_event_mux.py::test_event_ordering -v

# Pattern matching
pytest tests/ -k "backtest" -v
```

### Run with Coverage

```bash
pytest tests/ --cov=src --cov-report=html
# View coverage report at htmlcov/index.html
```

### Test Categories

| Category | Files | Focus |
|----------|-------|-------|
| Backtester | `test_backtester_*.py` | Execution, funding |
| Events | `test_event_*.py` | Event system |
| Strategy | `test_strategy_*.py` | Signal generation |
| Data | `test_data_*.py`, `test_dataset_*.py` | Data handling |
| Integration | `test_*_integration.py` | End-to-end |

---

## Code Style

### Formatting

```bash
# Format code
black src/ tests/

# Check formatting
black src/ tests/ --check
```

### Linting

```bash
# Run linter
ruff check src/ tests/

# Auto-fix
ruff check src/ tests/ --fix
```

### Type Checking

```bash
# Run mypy
mypy src/
```

### Pre-commit (recommended)

```bash
# Install hooks
pip install pre-commit
pre-commit install

# Run manually
pre-commit run --all-files
```

### Style Guidelines

- **Line length**: 100 characters
- **Imports**: Use `ruff` for sorting
- **Type hints**: Required for public functions
- **Docstrings**: Google style for modules/classes
- **Variable names**: Snake case (`my_variable`)
- **Constants**: Upper case (`MAX_LEVERAGE`)

---

## Common Development Tasks

### Add a New Configuration Field

1. Edit `src/config/settings.py`:
   ```python
   class MyConfig(BaseModel):
       new_field: str = Field(default="value", ge=0, le=100)
   ```

2. Add to `config.yaml`:
   ```yaml
   my_section:
     new_field: value
   ```

3. Add test in `tests/test_config.py`

### Add a New Event Type

1. Edit `src/ledger/events.py`:
   ```python
   class EventType(Enum):
       MY_NEW_EVENT = "MY_NEW_EVENT"
   ```

2. Register handler in `src/main.py`:
   ```python
   event_bus.register(EventType.MY_NEW_EVENT, handler)
   ```

3. Publish event:
   ```python
   await event_bus.publish(EventType.MY_NEW_EVENT, {"data": value})
   ```

### Add a New Technical Indicator

1. Edit `src/features/indicators.py`:
   ```python
   def compute_my_indicator(close: pd.Series, period: int) -> pd.Series:
       return ...
   ```

2. Add to `src/features/pipeline.py`:
   ```python
   df["my_indicator"] = compute_my_indicator(df["close"], period)
   ```

3. Add to configuration if parameterized

### Add a New CLI Tool

1. Create `src/tools/my_tool.py`:
   ```python
   import argparse

   def main():
       parser = argparse.ArgumentParser()
       parser.add_argument("--arg", required=True)
       args = parser.parse_args()
       # Tool logic

   if __name__ == "__main__":
       main()
   ```

2. Add to `pyproject.toml` (optional):
   ```toml
   [project.scripts]
   my-tool = "src.tools.my_tool:main"
   ```

3. Run: `python -m src.tools.my_tool --arg value`

---

## Debugging

### Enable Debug Logging

```yaml
# config.yaml
monitoring:
  log_level: DEBUG
  log_http: true
  log_http_responses: true
```

### View Strategy Decisions

```bash
# Watch thinking log
tail -f logs/thinking.jsonl | jq

# Filter by symbol
tail -f logs/thinking.jsonl | jq 'select(.symbol == "BTCUSDT")'
```

### View Event Ledger

```bash
# Recent events
tail -20 data/ledger/events.jsonl | jq

# Filter by type
grep "ORDER_FILLED" data/ledger/events.jsonl | jq
```

### Debug with breakpoints

```python
# Add breakpoint in code
import pdb; pdb.set_trace()

# Or use debugger in IDE
```

### Check Prometheus Metrics

```bash
# All metrics
curl http://localhost:9090/metrics

# Specific metric
curl -s http://localhost:9090/metrics | grep loop_last_tick_age_sec
```

### Common Issues

| Issue | Debug Steps |
|-------|-------------|
| No signals | Check `logs/thinking.jsonl` for reasons |
| Orders not filling | Check `logs/orders.csv` and event ledger |
| Strategy paused | Check `requires_manual_review` in state |
| API errors | Enable `log_http: true` in config |

---

## IDE Setup

### VS Code

Recommended extensions:
- Python (Microsoft)
- Pylance
- Black Formatter
- Ruff

`.vscode/settings.json`:
```json
{
    "python.defaultInterpreterPath": ".venv/bin/python",
    "editor.formatOnSave": true,
    "python.formatting.provider": "black",
    "python.linting.enabled": true,
    "python.linting.ruffEnabled": true
}
```

### PyCharm

1. Set Python interpreter to `.venv`
2. Enable Black formatter
3. Enable mypy plugin
4. Configure pytest as test runner

---

## Related Documentation

- [System Overview](../00_architecture/01_SystemOverview.md) - Architecture
- [Module Reference](../00_architecture/02_ModuleReference.md) - Code organization
- [Adding Strategies](02_AddingStrategies.md) - Strategy development
- [Event System](03_EventSystem.md) - Event sourcing
