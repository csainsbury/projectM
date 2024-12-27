#!/bin/bash

# Set up conda environment
export PATH="/Users/csainsbury/anaconda3/bin:$PATH"
source activate projectM

# Set Python path
export PYTHONPATH="/Users/csainsbury/Documents/projectM:$PYTHONPATH"

# Start the server
exec /Users/csainsbury/anaconda3/envs/projectM/bin/python /Users/csainsbury/Documents/projectM/projectM.py 