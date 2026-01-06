# Deployment Guide

Guide to deploying the Apollo in various environments.

## Prerequisites

- Python 3.10+ (3.11 or 3.12 recommended)
- pip or pipx
- Git (for cloning)
- Network access to Binance APIs

---

## Installation Methods

### Standard Installation

```bash
# Clone repository
git clone <repository-url>
cd apollo

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install package
pip install -e .
```

### Development Installation

```bash
pip install -e ".[dev]"
```

### With LLM Support

```bash
pip install -e ".[llm]"
```

---

## Environment Configuration

### 1. Create Environment File

```bash
cp .env.example .env
```

### 2. Configure API Keys

Edit `.env`:

```bash
# Testnet keys (for testnet mode)
BINANCE_TESTNET_FUTURE_API_KEY=your_testnet_key
BINANCE_TESTNET_FUTURE_SECRET_KEY=your_testnet_secret

# Live keys (for live mode)
BINANCE_API_KEY=your_live_key
BINANCE_SECRET_KEY=your_live_secret

# Optional: LLM keys for news classification
OPENAI_API_KEY=your_openai_key
# or
ANTHROPIC_API_KEY=your_anthropic_key
```

### 3. Configure Application

Edit `config.yaml`:

```yaml
# Run mode
run:
  mode: paper          # paper | testnet | live
  enable_trading: false
  live_confirm: ""     # Set to "YES_I_UNDERSTAND" for live

# Risk settings (adjust carefully)
risk:
  risk_per_trade_pct: 0.75
  max_leverage: 5
  max_drawdown_pct: 10.0
  max_positions: 1

# Monitoring
monitoring:
  metrics_port: 9090
  api_port: 8000
  log_level: INFO
```

---

## Environment Variables Reference

| Variable | Description | Required For |
|----------|-------------|--------------|
| `BINANCE_API_KEY` | Live API key | live mode |
| `BINANCE_SECRET_KEY` | Live API secret | live mode |
| `BINANCE_TESTNET_FUTURE_API_KEY` | Testnet API key | testnet mode |
| `BINANCE_TESTNET_FUTURE_SECRET_KEY` | Testnet API secret | testnet mode |
| `OPENAI_API_KEY` | OpenAI API key | LLM classifier |
| `ANTHROPIC_API_KEY` | Anthropic API key | LLM classifier |
| `CONFIG_PATH` | Custom config file path | optional |
| `RUN_MODE` | Override run.mode | optional |
| `RUN_ENABLE_TRADING` | Override run.enable_trading | optional |
| `RUN_LIVE_CONFIRM` | Override run.live_confirm | optional |

---

## Mode-Specific Setup

### Paper Mode

No API keys required. Uses simulated execution.

```yaml
run:
  mode: paper
  enable_trading: false
```

```bash
bot
```

### Testnet Mode

Requires testnet API keys.

1. Create testnet account at https://testnet.binancefuture.com
2. Generate API keys
3. Configure:

```yaml
run:
  mode: testnet
  enable_trading: true
```

```bash
bot
```

### Live Mode

**CAUTION**: Uses real funds.

1. Ensure testnet validation is complete
2. Configure live API keys
3. Set confirmation:

```yaml
run:
  mode: live
  enable_trading: true
  live_confirm: YES_I_UNDERSTAND
```

---

## Production Deployment

### Linux (systemd)

#### 1. Create Service User

```bash
sudo useradd -m -s /bin/bash trader
sudo -u trader bash
cd ~
git clone <repository-url> apollo
cd apollo
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

#### 2. Configure Environment

```bash
cp .env.example .env
nano .env  # Add API keys
cp config.yaml config_live.yaml
nano config_live.yaml  # Configure for live
```

#### 3. Create systemd Service

`/etc/systemd/system/binance-bot.service`:

```ini
[Unit]
Description=Apollo
After=network.target

[Service]
Type=simple
User=trader
Group=trader
WorkingDirectory=/home/trader/apollo
Environment=CONFIG_PATH=/home/trader/apollo/config_live.yaml
ExecStart=/home/trader/apollo/.venv/bin/bot
Restart=on-failure
RestartSec=30
StandardOutput=append:/home/trader/apollo/logs/bot_stdout.log
StandardError=append:/home/trader/apollo/logs/bot_stderr.log

[Install]
WantedBy=multi-user.target
```

#### 4. Enable and Start

```bash
sudo systemctl daemon-reload
sudo systemctl enable binance-bot
sudo systemctl start binance-bot
sudo systemctl status binance-bot
```

#### 5. View Logs

```bash
sudo journalctl -u binance-bot -f
tail -f /home/trader/apollo/logs/bot_stdout.log
```

### Docker (Optional)

#### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
COPY strategies/ strategies/
COPY config.yaml .

RUN pip install --no-cache-dir -e .

CMD ["bot"]
```

#### docker-compose.yml

```yaml
version: '3.8'
services:
  bot:
    build: .
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
      - ./config.yaml:/app/config.yaml:ro
    ports:
      - "9090:9090"  # Prometheus
      - "8000:8000"  # API
```

```bash
docker-compose up -d
docker-compose logs -f bot
```

---

## Directory Structure

Ensure these directories exist:

```bash
mkdir -p data/ledger data/state data/market logs
```

| Directory | Purpose | Permissions |
|-----------|---------|-------------|
| `data/ledger/` | Event ledger | Read/Write |
| `data/state/` | Persisted state | Read/Write |
| `data/market/` | Market data | Read/Write |
| `logs/` | Log files | Read/Write |

---

## Firewall Configuration

### Required Outbound

| Destination | Port | Protocol | Purpose |
|-------------|------|----------|---------|
| fapi.binance.com | 443 | HTTPS | REST API |
| fstream.binance.com | 443 | WSS | WebSocket |
| testnet.binancefuture.com | 443 | HTTPS | Testnet REST |
| stream.binancefuture.com | 443 | WSS | Testnet WebSocket |
| www.coindesk.com | 443 | HTTPS | News RSS |

### Optional Inbound

| Port | Purpose | Note |
|------|---------|------|
| 9090 | Prometheus metrics | For monitoring |
| 8000 | Operator API | For control |

---

## Health Checks

### Basic Check

```bash
curl http://localhost:8000/health
```

### Prometheus Metrics

```bash
curl http://localhost:9090/metrics | grep loop_last_tick_age_sec
```

### Process Check

```bash
# Linux
pgrep -f "bot"

# Check lock file
ls -la logs/bot.*.lock
```

---

## Backup and Recovery

### Backup Critical Files

```bash
#!/bin/bash
# backup.sh
BACKUP_DIR=/backup/binance_bot/$(date +%Y%m%d)
mkdir -p $BACKUP_DIR

cp config.yaml $BACKUP_DIR/
cp .env $BACKUP_DIR/
cp -r data/ledger/ $BACKUP_DIR/ledger/
cp -r data/state/ $BACKUP_DIR/state/
```

### Recovery

```bash
# Stop bot
sudo systemctl stop binance-bot

# Restore state
cp -r /backup/binance_bot/20240115/ledger/* data/ledger/
cp -r /backup/binance_bot/20240115/state/* data/state/

# Start bot
sudo systemctl start binance-bot
```

---

## Troubleshooting

### Bot Won't Start

1. Check Python version: `python --version`
2. Check virtual environment: `which python`
3. Check config: `python -c "from src.config.settings import load_settings; print(load_settings())"`
4. Check lock file: `rm logs/bot.*.lock` (if stale)

### Connection Issues

1. Test Binance connectivity:
   ```bash
   curl https://fapi.binance.com/fapi/v1/ping
   ```
2. Check firewall rules
3. Check API key validity

### Memory Issues

1. Check memory usage: `ps aux | grep bot`
2. Consider log rotation
3. Archive old event ledger entries

---

## Related Documentation

- [RUNBOOK](../../RUNBOOK.md) - Operations procedures
- [Configuration Reference](../04_reference/01_Configuration.md) - All config options
- [Monitoring Guide](02_Monitoring.md) - Monitoring setup
