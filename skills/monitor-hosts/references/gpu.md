# Watching an NVIDIA GPU machine

A GPU box fails differently from a web server: it overheats, the driver loses the card, a job dies quietly and the
card sits idle while you think it is working, or memory fills and the next job cannot start. infrawatch reads
`nvidia-smi` once a minute; nothing else is installed.

## What to turn on

```ini
[gpu]
# true on a machine whose whole purpose is the GPU: then a missing driver is an alert
enabled = auto
# see "Idle" below
idle_minutes = 0
idle_util = 5
# see "The job itself" below
processes =

[thresholds]
gpu_temp_warn = 85
gpu_temp_crit = 92
gpu_mem_warn = 90
gpu_mem_crit = 98
```

Comments go on their own lines: the config has no inline comments. Look at the card first: `nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw --format=csv`

## Temperature

85 / 92 °C suits most desktop and workstation cards under sustained load. Check the card's own limit
(`nvidia-smi -q | grep -i -A3 "Temperature"` shows the slowdown and shutdown temperatures on cards that report
them) and stay a few degrees under the slowdown temperature for critical. A card that idles at 40 °C and renders at
70 °C can take `gpu_temp_warn = 80`: the earlier warning is about a dead fan or blocked airflow, not the limit.

## Memory

A model server that loads its weights once sits at a constant, high memory use by design. If that is above 90 %,
raise `gpu_mem_warn` above it; the useful alert is the jump to 98 %, where the next request fails with out of
memory. For training or batch jobs that fill the card on purpose, set both to 101 to turn the check off.

## Idle: the job died

`idle_minutes = 30` warns when every GPU has been under `idle_util` percent for 30 minutes, and says when it is
busy again. Use it on a machine that should always be working (a render queue, a training run you are waiting on).
Leave it at 0 on a machine that serves requests on demand: idle is normal there.

## The job itself

`processes = python3, ffmpeg` alerts when no process with that name holds GPU memory. It catches the service that
restarted and fell back to CPU, which every other check calls healthy. Names are matched as part of what
`nvidia-smi --query-compute-apps=process_name` prints; inside Docker that is the process name in the container.

Also list the service under `[services] units` or `containers`, and its health URL under `[http]`: a process on
the GPU can still be hung.

## Xid errors

The driver logs an `NVRM: Xid` line in the kernel log when something goes wrong with the card. infrawatch tells
each new one once, and marks the codes that usually mean the card needs a reset or the machine a reboot:

| Xid | Usually |
|---|---|
| 79 | GPU has fallen off the bus: power, riser, or a dying card. Reboot; if it returns, look at hardware |
| 48 | Double-bit ECC error: reset the GPU |
| 62, 64, 74, 94, 95, 109, 119, 120 | Internal, memory or link errors that need a GPU reset or reboot |
| 13, 31, 43 | An application fault (a bad shader, an illegal memory access): the job crashed, the card is fine |

NVIDIA's catalog: <https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html>

## Things to know

- `nvidia-smi` not answering is itself critical (`gpu:smi`), and so is a GPU that disappears from the list
  (`gpu:count`): both are how a card falling off the bus looks from the OS.
- Fan speed is not checked: what the driver reports is the speed it asked for, not what the fan does. A dead fan
  shows up as temperature.
- Asking `nvidia-smi` every minute keeps an otherwise idle card from reaching its deepest power-saving state on
  some systems. If idle power draw matters more than minute resolution, run the timer less often
  (`OnUnitActiveSec=5min`) on that machine.
- Consumer RTX cards report no ECC counters; values they do not report show as `[N/A]` and are skipped.
- Several GPUs: each gets its own `gpu<N>:temp` and `gpu<N>:mem`; idle means all of them.
