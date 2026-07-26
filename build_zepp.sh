#!/bin/bash
set -e
export PATH=/tmp/node18/bin:$PATH
cd /home/aldous/vex/vex_voice/zepp

# Install deps
npm install 2>&1 | tail -2

# Fix zeus-cli ESM deps
ZNM=node_modules/@zeppos/zeus-cli/node_modules
rm -rf $ZNM/package-json $ZNM/got 2>/dev/null
cd $ZNM
npm install package-json@6.1.0 got@11.8.6 2>&1 | tail -2
cd /home/aldous/vex/vex_voice/zepp

# Build
npx zeus build 2>&1
echo "---"
ls -la dist/ 2>/dev/null || echo "No dist/"
