"""
=====================================================
Proof-of-Concept Offline Reinforcement Learning Demo
for a Prescribing Decision Model
=====================================================

Author: [Your Name]
Date: [Today's Date]

Description:
------------
This Python script illustrates how to implement a basic
offline RL prototype for prescribing decisions, using
the dataset structure described.

Features:
 1. Loads CSV data from /data
 2. Builds minimal "patient state" representations
 3. Maps drug names to discrete actions
 4. Defines a simple environment-like class
 5. Demonstrates a placeholder policy
 6. Implements a toy reward function
 7. Shows how to do a naive "search" and "learning" step

-----------------------------------------------------
NOTE: This is purely a demonstration. It does not reflect
the complexity of real-world medical prescribing.
-----------------------------------------------------
"""

import os
import pandas as pd
import numpy as np
import random
from typing import Dict, List, Tuple

# Optional: If you want to illustrate a simple neural policy, you could use Torch
# import torch
# import torch.nn as nn
# import torch.optim as optim


# -----------------------------------------------------------------
# 1. LOADING & MERGING DATA
# -----------------------------------------------------------------
def load_data(data_dir: str = "./data"):
    """
    Loads CSVs from the data directory and returns Pandas DataFrames.
    We assume that:
      - hba1c.csv has columns: [unique_id, Datetime_stamp, value]
      - bmi.csv    has columns: [unique_id, Datetime_stamp, value]
      - sbp.csv    has columns: [unique_id, Datetime_stamp, value]
      - demog.csv  has columns: [unique_id, date_of_birth, sex, ethnicity]
      - prescription.csv has columns: [unique_id, Datetime_stamp, drug_name]
      - mortality.csv has columns: [unique_id, date_of_death]
    """
    hba1c_path = os.path.join(data_dir, "hba1c.csv")
    bmi_path = os.path.join(data_dir, "bmi.csv")
    sbp_path = os.path.join(data_dir, "sbp.csv")
    demog_path = os.path.join(data_dir, "demog.csv")
    rx_path = os.path.join(data_dir, "prescription.csv")
    mort_path = os.path.join(data_dir, "mortality.csv")

    df_hba1c = pd.read_csv(hba1c_path) if os.path.exists(hba1c_path) else pd.DataFrame()
    df_bmi = pd.read_csv(bmi_path) if os.path.exists(bmi_path) else pd.DataFrame()
    df_sbp = pd.read_csv(sbp_path) if os.path.exists(sbp_path) else pd.DataFrame()
    df_demog = pd.read_csv(demog_path) if os.path.exists(demog_path) else pd.DataFrame()
    df_rx = pd.read_csv(rx_path) if os.path.exists(rx_path) else pd.DataFrame()
    df_mort = pd.read_csv(mort_path) if os.path.exists(mort_path) else pd.DataFrame()

    return df_hba1c, df_bmi, df_sbp, df_demog, df_rx, df_mort


def preprocess_data(
    df_hba1c: pd.DataFrame,
    df_bmi: pd.DataFrame,
    df_sbp: pd.DataFrame,
    df_demog: pd.DataFrame,
    df_rx: pd.DataFrame,
    df_mort: pd.DataFrame
):
    """
    Minimal example: merges mortality data with demographic info
    and creates a single table of "patient-level" features.
    More advanced code might pivot/aggregate timeseries, etc.
    """
    # We'll assume there's a 1:1 relationship for unique_id in demog and mortality
    # Merge demog + mortality on unique_id
    df_demo_mort = pd.merge(df_demog, df_mort, on="unique_id", how="left")

    # For labs (like hba1c, bmi, sbp), we might just keep the latest value
    # as a naive example. In reality, you'd have a time-based approach.
    def get_latest_value(df_lab: pd.DataFrame, lab_name: str) -> pd.DataFrame:
        if df_lab.empty:
            return pd.DataFrame(columns=["unique_id", lab_name])
        # Sort by Datetime_stamp descending, drop duplicates to keep last
        df_sorted = df_lab.sort_values(by=["unique_id", "Datetime_stamp"], ascending=False)
        df_sorted = df_sorted.drop_duplicates(subset=["unique_id"], keep="first")
        df_sorted = df_sorted.rename(columns={"value": lab_name})
        return df_sorted[["unique_id", lab_name]]

    df_hba1c_latest = get_latest_value(df_hba1c, "hba1c")
    df_bmi_latest = get_latest_value(df_bmi, "bmi")
    df_sbp_latest = get_latest_value(df_sbp, "sbp")

    # Merge all labs with df_demo_mort
    df_merged = pd.merge(df_demo_mort, df_hba1c_latest, on="unique_id", how="left")
    df_merged = pd.merge(df_merged, df_bmi_latest, on="unique_id", how="left")
    df_merged = pd.merge(df_merged, df_sbp_latest, on="unique_id", how="left")

    # Also keep prescriptions as separate time-series for later
    df_rx_sorted = df_rx.sort_values(by=["unique_id", "Datetime_stamp"])

    return df_merged, df_rx_sorted


# -----------------------------------------------------------------
# 2. ACTION SPACE (drug_name -> discrete action)
# -----------------------------------------------------------------
def build_action_map(df_rx: pd.DataFrame) -> Dict[str, int]:
    """
    Maps unique drug_name strings to integer actions (0, 1, 2, ...).
    """
    unique_drugs = df_rx["drug_name"].unique().tolist()
    action_map = {drug: i for i, drug in enumerate(unique_drugs)}
    return action_map


# -----------------------------------------------------------------
# 3. ENVIRONMENT-LIKE CLASS
# -----------------------------------------------------------------
class OfflinePrescribingEnv:
    """
    A minimal offline environment for prescribing:

    - We store a set of patient states, each with a 'current' state vector
      (labs, demographics).
    - We store the possible actions (drug_name).
    - We store outcome data for a reward function.

    For simplicity, we simulate:
      - stepping through each patient exactly once
      - prescribing a single action
      - awarding a reward based on mortality
    """

    def __init__(
        self,
        df_merged: pd.DataFrame,
        df_rx: pd.DataFrame,
        action_map: Dict[str, int]
    ):
        """
        df_merged: merged table of patient-level features + mortality
        df_rx: full prescription timeseries (unused in this naive example,
               but available if you want to do multi-step sequences)
        action_map: dictionary mapping drug_name -> action_id
        """
        self.df_merged = df_merged.copy()
        self.df_rx = df_rx.copy()
        self.action_map = action_map

        # We'll treat each row in df_merged as a single "patient"
        self.patients = self.df_merged["unique_id"].unique().tolist()
        self.index = 0  # track the current patient index

    def reset(self):
        """Resets environment to start from the first patient."""
        self.index = 0

    def get_state(self, patient_id: str) -> np.ndarray:
        """
        Returns a simple numeric state representation for a given patient:
        [age, sex_code, ethnicity_code, hba1c, bmi, sbp].
        For sex/ethnicity, we do naive code to numeric.
        If any value missing, we set to zero or a default.
        """
        row = self.df_merged[self.df_merged["unique_id"] == patient_id].iloc[0]

        # Simple numeric encoding for sex
        sex_code = 1.0 if row["sex"] == "M" else 0.0  # or fancier
        # Simple numeric encoding for ethnicity, demonstration only
        ethnicity_map = {"White": 0.0, "Black": 1.0, "Asian": 2.0, "Other": 3.0}
        eth_code = ethnicity_map.get(row["ethnicity"], 3.0)

        # Age - if we want a naive approach, we can try year difference
        # or just store a placeholder
        # For real code, you'd parse date_of_birth, compare with some index date
        age_placeholder = 60.0

        hba1c = row["hba1c"] if pd.notnull(row["hba1c"]) else 0.0
        bmi = row["bmi"] if pd.notnull(row["bmi"]) else 0.0
        sbp = row["sbp"] if pd.notnull(row["sbp"]) else 0.0

        state_array = np.array([age_placeholder, sex_code, eth_code, hba1c, bmi, sbp])
        return state_array

    def step(self, action_id: int) -> Tuple[float, bool]:
        """
        For the current patient, we assume we get a reward based on mortality.
        Then move to the next patient. We do a single-step episode for each patient.

        Returns:
         reward: float
         done: bool, True if no more patients
        """
        patient_id = self.patients[self.index]
        row = self.df_merged[self.df_merged["unique_id"] == patient_id].iloc[0]

        # Reward: +1 if alive, 0 if dead (toy example).
        # If date_of_death is present, reward=0
        dead = pd.notnull(row["date_of_death"])
        reward = 0.0 if dead else 1.0

        # Move to next
        self.index += 1
        done = (self.index >= len(self.patients))

        return reward, done


# -----------------------------------------------------------------
# 4. POLICY & SEARCH
# -----------------------------------------------------------------
class RandomPolicy:
    """
    A trivial policy that picks an action at random
    from the available action space.
    """

    def __init__(self, num_actions: int):
        self.num_actions = num_actions

    def select_action(self, state: np.ndarray) -> int:
        """
        Ignores state, just picks a random action ID.
        """
        return random.randint(0, self.num_actions - 1)


def search_best_action(env: OfflinePrescribingEnv, policy, n_candidates: int = 5):
    """
    Toy 'search' routine. For the current patient, sample
    multiple actions from the policy, pick the one that yields
    the highest *expected reward*.

    Because environment is offline and we do single-step episodes,
    we can't truly roll out multiple actions for the same patient
    in a real-time sense. We'll simulate it by:
      1) for each candidate action, approximate reward with a reward model
         (here we cheat by looking at mortality in step() directly).
      2) pick best action based on reward.

    In a real offline scenario, you'd have a learned reward model or
    a simulator, not direct environment calls.

    Returns the best action_id found, plus the best reward.
    """
    current_index = env.index
    if current_index >= len(env.patients):
        # No more patients
        return None, None

    # We'll do a simplistic approach: for each candidate
    # action, do a "dry run" stepping. Then restore the env index.
    best_a = None
    best_reward = -1.0

    patient_id = env.patients[current_index]
    state = env.get_state(patient_id)

    for _ in range(n_candidates):
        candidate_a = policy.select_action(state)
        # step forward
        rew, _ = env.step(candidate_a)
        # check
        if rew > best_reward:
            best_reward = rew
            best_a = candidate_a
        # revert environment index
        env.index = current_index

    return best_a, best_reward


# -----------------------------------------------------------------
# 5. LEARNING (Behavior Cloning Example)
# -----------------------------------------------------------------
class SimpleBCPolicy:
    """
    A naive Behavior Cloning policy that stores a dictionary of
    (state -> best_action) discovered so far. Real BC would
    typically use parametric function approximators (NNs).
    """

    def __init__(self, num_actions: int):
        self.num_actions = num_actions
        self.state_dict = {}  # map from tuple(state) -> action_id

    def select_action(self, state: np.ndarray) -> int:
        key = tuple(state.round(2))  # round to reduce floating uniqueness
        return self.state_dict.get(key, random.randint(0, self.num_actions - 1))

    def update(self, state: np.ndarray, best_action: int):
        """
        Behavior Cloning update: record the best action for that state.
        """
        key = tuple(state.round(2))
        self.state_dict[key] = best_action


def run_episode_with_bc(env: OfflinePrescribingEnv, search_policy, bc_policy, n_candidates=5):
    """
    Demonstrates:
     1) For each patient, we do a toy search to pick the best action
     2) We store that as "expert" action
     3) We do a BC update
    """
    env.reset()
    total_reward = 0.0
    done = False

    while not done:
        # search for best action
        best_a, best_r = search_best_action(env, search_policy, n_candidates=n_candidates)
        if best_a is None:
            # no more patients
            break
        total_reward += best_r

        # do a BC update so that bc_policy learns
        patient_id = env.patients[env.index]
        state = env.get_state(patient_id)
        bc_policy.update(state, best_a)

        # actually step env with best_a
        rew, done = env.step(best_a)

    return total_reward


def evaluate_bc(env: OfflinePrescribingEnv, bc_policy):
    """
    Evaluate the BC policy. We'll step through each patient once,
    letting bc_policy select the action. Then measure total reward.
    """
    env.reset()
    total_reward = 0.0
    done = False
    while not done:
        patient_id = env.patients[env.index]
        state = env.get_state(patient_id)
        action_id = bc_policy.select_action(state)
        rew, done = env.step(action_id)
        total_reward += rew

    return total_reward


# -----------------------------------------------------------------
# MAIN DEMO
# -----------------------------------------------------------------
def main():
    # 1. Load data
    df_hba1c, df_bmi, df_sbp, df_demog, df_rx, df_mort = load_data("./data")

    # 2. Merge/preprocess
    df_merged, df_rx_sorted = preprocess_data(df_hba1c, df_bmi, df_sbp, df_demog, df_rx, df_mort)

    # Build action map
    action_map = build_action_map(df_rx_sorted)
    num_actions = len(action_map)

    # 3. Create environment
    env = OfflinePrescribingEnv(df_merged, df_rx_sorted, action_map)

    # 4. Initialize a random search policy
    search_policy = RandomPolicy(num_actions=num_actions)

    # 5. Behavior Cloning policy (initially random)
    bc_policy = SimpleBCPolicy(num_actions=num_actions)

    # Show training loop
    n_epochs = 3
    for epoch in range(n_epochs):
        # run an episode
        total_reward = run_episode_with_bc(env, search_policy, bc_policy, n_candidates=5)
        print(f"[TRAIN EPOCH {epoch+1}] Total Reward from 'search+BC': {total_reward}")

        # Evaluate BC policy
        eval_reward = evaluate_bc(env, bc_policy)
        print(f"[EVAL EPOCH {epoch+1}] BC Policy Reward: {eval_reward}")

    print("Training complete. End of demonstration.")


if __name__ == "__main__":
    main()