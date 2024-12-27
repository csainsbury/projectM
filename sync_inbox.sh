#!/bin/bash
export PATH="/Users/csainsbury/anaconda3/envs/projectM/bin:$PATH"
source activate projectM
/Users/csainsbury/anaconda3/envs/projectM/bin/python -c "from projectM.projectM import sync_local_to_github; sync_local_to_github('inbox')"

