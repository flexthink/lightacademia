#!/bin/bash
# %%exp: repo=https://git:%token%@github.com/flexthink/wavslm-eval.git
# %%exp: time=8:00:00
# %%exp: tasks=4
# %%exp: checkout=main
# %%exp: gpu-type=medium

. $HOME/scripts/experiments/common.sh

use-speechbrain
use-python $EXPERIMENT_SRC/src
prepare-ljspeech

prepare-slm21-lexical
prepare-slm21-syntactic
prepare-tsc

cd $EXPERIMENT_SRC/recipes/LibriSpeech/WavSLM

python eval.py hparams/eval.yaml \
    --device cuda \
    --output_folder $EXPERIMENT_OUTPUT \
    --swuggy_data_folder $SLM21_LEXICAL_PATH \
    --sblimp_data_folder $SLM21_SYNTACTIC_PATH \
    --tsc_data_folder $TSC_PATH \
    --eval_dataset dev

# LightAcademia
