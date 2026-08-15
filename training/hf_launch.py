from __future__ import annotations

from dataclasses import dataclass

from .hf_job_guard import HFTrainingLaunchGuard
from .train_sft import SFTTrainingRequest, train_sft


@dataclass(frozen=True)
class HFTrainingLaunchRequest:
    training: SFTTrainingRequest
    guard: HFTrainingLaunchGuard

    def validate(self) -> None:
        self.guard.validate()
        self.training.validate()
        if self.training.base_model_id != self.guard.base_model_id:
            raise ValueError("training_guard_model_mismatch")
        if self.training.base_revision != self.guard.base_revision:
            raise ValueError("training_guard_revision_mismatch")
        if self.training.adapter_mode != self.guard.adapter_mode:
            raise ValueError("training_guard_adapter_mismatch")
        if self.training.lora_r != self.guard.lora_r:
            raise ValueError("training_guard_lora_r_mismatch")
        if self.training.lora_alpha != self.guard.lora_alpha:
            raise ValueError("training_guard_lora_alpha_mismatch")
        if self.training.lora_dropout != self.guard.lora_dropout:
            raise ValueError("training_guard_lora_dropout_mismatch")
        if tuple(self.training.lora_target_modules) != tuple(self.guard.lora_target_modules):
            raise ValueError("training_guard_lora_target_modules_mismatch")


def launch_training(request: HFTrainingLaunchRequest) -> None:
    """Canonical HF training entrypoint.

    All HF learning launches must pass the no-repeat guard before the expensive
    training stack is imported or model weights are loaded.
    """

    request.validate()
    train_sft(request.training)
