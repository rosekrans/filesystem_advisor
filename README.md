# filesystem_advisor

A careful, local-first filesystem cleanup advisor that uses a local Ollama model to analyze disk usage data and suggest safe cleanups.

It never deletes anything — it only advises. Every run is saved to a history directory so context can be continued later.

## Requirements

- Python 3.9+
- [Ollama](https://ollama.com/) running locally
- The model you want to use (default: `phi3:3.8b`)

```bash
# Install the Python dependency
pip install -r requirements.txt

# Pull the default model (or change --model)
ollama pull phi3:3.8b
```

## Usage

```bash
# New scan + analysis
python filesystem_advisor.py

# Continue previous context with a fresh scan
python filesystem_advisor.py --continue

# Feed external data (JSON or plain text)
python filesystem_advisor.py --data mydata.json

# Enter interactive chat after the analysis
python filesystem_advisor.py --chat

# List previous sessions
python filesystem_advisor.py --list-sessions

# Override the default scan paths
python filesystem_advisor.py --scan ~/.cache /tmp ~/Downloads
```

Sessions are saved under `fs_advisor_history/` as JSON files.

## Safety

The script is read-only with respect to your filesystem. It only gathers size information and asks the model for advice. Always review any commands the model suggests before running them.

## License

MIT
