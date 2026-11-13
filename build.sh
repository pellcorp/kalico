#!/bin/bash

# in case build is executed from outside current dir be a gem and change the dir
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd -P)"
cd $SCRIPT_DIR

mkdir -p outfw/
rm -rf outfw/*

#./_build.sh host || exit $?
./_build.sh btteddy || exit $?
./_build.sh e3v3se || exit $?
./_build.sh e3v3se-c14 || exit $?
./_build.sh creality-42x || exit $?

mv outfw/btteddy.uf2 fw/K1/
mv outfw/*.bin fw/NEBULA/
