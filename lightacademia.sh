#!/bin/zsh

# Run from any directory, using the active environment or an explicit interpreter.
cd "${0:A:h}" || exit 1
exec "${LIGHTACADEMIA_PYTHON:-python3}" -m lightacademia \
  --notebook "$PWD/notebook" --tools "$PWD/tools" "$@"
