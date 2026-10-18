# Run 6 part A on a GPU VM

The self-hosted `pplx-flash-local` row (PROTOCOL.md, "Run 6 addendum, part A"). One NVIDIA L4 (24 GB), the vendor's
`pplx_decider` package unmodified, BF16, served by `serve/systemone_server.py`; the benchmark client runs on the same VM
so the network leg is loopback. Everything after the VM is up is `scripts/run6_gpu.sh`.

Budget: about 3 hours of VM time end to end. The ×1 load test alone replays 600 PRs at 0.35/s (~29 min); the
single-question pass is 7,050 calls. At $0.80/h that is under $3.

## 1. AWS (reference setup)

| setting | value |
|---|---|
| instance | `g6.xlarge`: 1 × L4 24 GB, 4 vCPU, 16 GiB. On-demand us-east-1 $0.8048/h when written; check the current price and pass it as `USD_PER_HOUR` |
| AMI | **Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)**, the latest one |
| disk | 60 GB gp3 root volume (checkpoint ~9 GB + pip cache for torch) |
| network | security group with **inbound SSH (22) from your IP only**; nothing else open (the server listens on 127.0.0.1) |
| quota | "Running On-Demand G and VT instances" ≥ 4 vCPU in the region (Service Quotas; new accounts often have 0) |

Console: EC2 → Launch instance → search AMIs for "Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)" →
`g6.xlarge` → key pair → network settings: allow SSH from "My IP" only → storage 60 GiB gp3 → Launch.

CLI equivalent (fill in the key pair and security group):

```bash
REGION=us-east-1
AMI=$(aws ssm get-parameter --region $REGION \
  --name /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id --query Parameter.Value --output text)
aws ec2 run-instances --region $REGION --image-id "$AMI" --instance-type g6.xlarge --key-name <your-key> \
  --security-group-ids <sg-with-ssh-from-your-ip-only> \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=60,VolumeType=gp3,DeleteOnTermination=true}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=mergeone-run6}]'
```

Log in with `ssh -i <key>.pem ubuntu@<public-ip>`. `nvidia-smi` must show the L4.

## 1b. RunPod (alternative)

Deploy a pod with **1 × L4**, a PyTorch / CUDA 12.x Ubuntu 22.04 template, ≥ 60 GB volume, SSH enabled (no HTTP ports
needed). RunPod has no EC2 metadata, so set `VM_TYPE="runpod L4 <secure|community> cloud"` and `USD_PER_HOUR=<the pod's hourly price>`
before running the script. Everything below is the same.

## 2. Repository, Python, data

```bash
git clone https://github.com/haalp/mergeone && cd mergeone        # at the commit that holds serve/ and this file
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.local/bin/env
uv venv --seed --python 3.12 .venv                                 # mergeone needs Python >= 3.11; Ubuntu 22.04 ships 3.10
# optional: echo "HF_TOKEN=hf_..." > .env   # the repo is public; a token only raises the download rate limit
```

The dataset is not in git. From the machine that has it (the run-5 Mac):

```bash
scp -i <key>.pem data/run3/dataset.jsonl data/run3/manifest.json ubuntu@<ip>:mergeone/data/run3/
scp -i <key>.pem results/run4/pairs.jsonl ubuntu@<ip>:mergeone/results/run4/     # optional: the same_change pair task (R6A-H3)
```

(`mkdir -p mergeone/data/run3` on the VM first.) Without `results/run4/pairs.jsonl` the pair task is skipped and reported "not run".

## 3. Run

```bash
tmux new -s run6
mkdir -p results/run6/logs
USD_PER_HOUR=0.8048 bash scripts/run6_gpu.sh 2>&1 | tee -a results/run6/logs/run6_gpu.out
```

Stages, in R6A.5 order (re-run the script after any failure; finished work is skipped):

1. `setup`: `pip install -e .`, `serve/requirements.txt`, `snapshot_download("perplexity-ai/pplx-decider-flash-v1.2")`
   (the resolved commit is written to `results/run6/logs/checkpoint_dir` and reused, so the revision cannot move mid-run),
   then `pip install "<ckpt>/source[fast]"` (pins torch 2.14 / transformers 5.17).
2. `serve`: starts the server in the background (`results/run6/logs/server.log`), waits for `/healthz`.
   `results/run6/serve_meta.json` records model, revision, GPU, versions, VM type/region, `$/h`.
3. `h1`: joint v3 on the 705 test PRs (equivalence set). `val`: joint v3 on val (calibration refit for H3).
4. `single`: v3 test with `--single-question` (10 requests per PR; R6A-H4).
5. `pairs`: same_change on the run-4 R4-H2 test pairs (only if `results/run4/pairs.jsonl` was copied).
6. `load`: open-loop load tests at ×1, ×10, ×30, ×100, ×300 (0.35 → 104 decisions/s), 429 retry off, 512 workers,
   60 s between steps. `FORCE_LOADTEST=1` repeats the curve and keeps the previous pass under `results/run6/loadtest_passes/`.
7. `report`: `mergeone report results/run6 --run6` → `results/run6/metrics.json`, `tables.md`; `mergeone leaderboard`.
8. `stop`: stops the server.

Run single stages with `STAGES="load report"`, skip with `SKIP="pairs"`. Manual equivalents:

```bash
.venv/bin/python serve/systemone_server.py --checkpoint-dir "$(cat results/run6/logs/checkpoint_dir)" --port 8080 --usd-per-hour 0.8048 &
export MERGEONE_LOCAL_ENDPOINT=http://127.0.0.1:8080/v1/systemone
.venv/bin/python -m mergeone.cli predict --model pplx-flash-local --split test --run run6 --dataset data/run3/dataset.jsonl --qset v3 \
  --concurrency 16 --append --only-missing-from results/run6/predictions_pplx-flash-local_test_all.jsonl
MERGEONE_NO_RETRY=1 .venv/bin/python -m mergeone.cli loadtest --model pplx-flash-local --multiplier 100 --limit 600 --run run6 \
  --dataset data/run3/dataset.jsonl --qset v3 --no-retry --concurrency 512
```

## 4. Bring the results back, shut down

```bash
# on your machine
scp -r -i <key>.pem ubuntu@<ip>:mergeone/results/run6 results/          # logs/ is gitignored; keep it locally if you want it
aws ec2 terminate-instances --region us-east-1 --instance-ids <id>      # R6A.5 step 7 — do not leave the L4 running
```

Commit `results/run6/` (predictions, load tests, `serve_meta.json`, `metrics.json`, `tables.md`) and the regenerated leaderboard.

## Troubleshooting

- `401`/`403` on download: the HF account behind `HF_TOKEN` has not accepted the gated licence for the repo.
- `CUDA driver version is insufficient`: the pinned torch needs a newer driver than the image has; use the latest Base OSS
  Nvidia Driver AMI.
- `/healthz` never comes up: `tail results/run6/logs/server.log`. A failed `from pplx_decider import Decider` means step 1's
  `pip install "<ckpt>/source[fast]"` did not run in the same venv.
- Load-test errors at ×300 are a measurement (saturation), not a failure of the script; the curve is reported as measured.
