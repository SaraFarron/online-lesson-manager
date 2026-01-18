#!/bin/bash

git fetch
if [ "$(git rev-parse HEAD)" != "$(git rev-parse main@{upstream})" ]; then
  echo "There are new commits on the remote main branch. Running update.sh..."
  ./scripts/update.sh
fi
