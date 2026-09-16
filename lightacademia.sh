#!/bin/zsh

cd "$HOME/Projects/lightacademia" || exit 1

source $HOME/venv/ml/bin/activate

streamlit run app.py --server.port 8599 --server.headless true -- --codex-agent gpt-5.6-luna &
echo $! > /tmp/lightacademia.pid

