#!/usr/bin/env bash
# Run the inventree_mcp test suite against a local InvenTree dev instance.
#
# Assumes it's being run inside the standard InvenTree devcontainer, where a
# working InvenTree checkout + venv + database are already available. Installs
# this plugin editable into that venv, activates it as a mandatory plugin, and
# runs the test suite via InvenTree's own manage.py.
#
# Usage:
#   ./run_tests.sh                                                  # full suite
#   ./run_tests.sh inventree_mcp.test_mcp.MCPTransportTest
#   ./run_tests.sh inventree_mcp.test_mcp --keepdb -v 2             # extra manage.py test args
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${INVENTREE_VENV:-/home/inventree/dev/venv}"
BACKEND_DIR="${INVENTREE_BACKEND_DIR:-/home/inventree/src/backend}"

TEST_TARGET="${1:-inventree_mcp.test_mcp}"
if [ "$#" -gt 0 ]; then
    shift
fi

"$VENV/bin/pip" install -e "$PLUGIN_DIR" --quiet

cd "$BACKEND_DIR"

INVENTREE_PLUGINS_ENABLED=True \
INVENTREE_PLUGINS_MANDATORY=inventree-mcp \
INVENTREE_PLUGIN_TESTING=True \
INVENTREE_PLUGIN_TESTING_SETUP=True \
"$VENV/bin/python" InvenTree/manage.py test "$TEST_TARGET" --keepdb "$@"
