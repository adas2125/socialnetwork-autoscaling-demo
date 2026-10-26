#!/usr/bin/env python3
"""Plot saved sweep latencies and service CPU. Requires matplotlib.

Usage: python3 scripts/plot-sn-knee.py [path/to/run-directory]
Outputs: plots/latency-sweep.png and plots/service-cpu-1200.png in that directory.
"""

import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def milliseconds(value):
    number, unit = re.fullmatch(r"([\d.]+)(ns|us|µs|ms|s|m)", value).groups()
    factors = {"ns": 0.000001, "us": 0.001, "µs": 0.001,
               "ms": 1, "s": 1000, "m": 60000}
    return float(number) * factors[unit]


def plot_latency(run_dir, output_dir):
    rates = [150, 300, 600, 1200]
    latencies = []
    for rate in rates:
        path = run_dir / "sweep" / f"rate-{rate}.txt"
        text = path.read_text()
        patterns = [r"^\s*Latency\s+(\S+)",
                    r"^\s*90\.0+%\s+(\S+)",
                    r"^\s*99\.0+%\s+(\S+)"]
        values = []
        for pattern in patterns:
            match = re.search(pattern, text, re.MULTILINE)
            if match is None:
                raise ValueError(f"Missing latency summary in {path}")
            values.append(milliseconds(match.group(1)))
        latencies.append(values)

    fig, ax = plt.subplots(figsize=(8, 5))
    width = 0.25
    for column, label in enumerate(["Mean", "p90", "p99"]):
        ax.bar([i + (column - 1) * width for i in range(len(rates))],
               [values[column] for values in latencies], width, label=label)
    ax.set_xticks(range(len(rates)), [str(rate) for rate in rates])
    ax.set_xlabel("Offered load (requests/second)")
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Compose-post latency sweep")
    ax.set_axisbelow(True)
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_dir / "latency-sweep.png", dpi=150)
    plt.close(fig)


def read_cpu(path):
    samples = []
    timestamp = None
    services = defaultdict(float)
    pod_section = False
    for line in path.read_text().splitlines():
        line = line.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", line):
            if timestamp is not None and services:
                samples.append((timestamp, dict(services)))
            timestamp = datetime.strptime(line, "%Y-%m-%dT%H:%M:%SZ")
            services = defaultdict(float)
            pod_section = False
        elif line.startswith("NAME"):
            # Node tables contain CPU(%); pod tables do not.
            pod_section = "CPU(cores)" in line and "CPU(%)" not in line
        elif pod_section and timestamp is not None:
            fields = line.split()
            if len(fields) != 3 or fields[0] == "sn-wrk2":
                continue
            pod, cpu, _ = fields
            cores = float(cpu[:-1]) / 1000 if cpu.endswith("m") else float(cpu)
            # Deployment pods end in a ReplicaSet hash and a pod suffix.
            service = pod.rsplit("-", 2)[0]
            services[service] += cores
    if timestamp is not None and services:
        samples.append((timestamp, dict(services)))
    if not samples:
        raise ValueError(f"No pod CPU samples found in {path}")
    return samples


def plot_cpu(run_dir, output_dir):
    samples = read_cpu(run_dir / "cpu-1600" / "cpu.txt")
    elapsed = [(timestamp - samples[0][0]).total_seconds()
               for timestamp, _ in samples]
    services = sorted({name for _, values in samples for name in values})
    fig, ax = plt.subplots(figsize=(14, 7))
    colors = plt.get_cmap("tab20")
    for i, service in enumerate(services):
        values = [cpu.get(service, float("nan")) for _, cpu in samples]
        ax.plot(elapsed, values, label=service, color=colors(i % 20),
                linestyle="-" if i < 20 else "--", linewidth=1.3)
    ax.set_xlabel("Seconds since CPU logging began")
    ax.set_ylabel("CPU cores (sum across replicas)")
    ax.set_title("Service CPU over time — 1,400 RPS experiment")
    ax.set_ylim(bottom=0)
    ax.grid(alpha=0.25)
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), fontsize=8)
    fig.tight_layout()
    fig.savefig(output_dir / "service-cpu-1400.png", dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_dir", nargs="?", type=Path,
        default=Path(__file__).resolve().parents[1]
        / "results/sn-knee/standalone-sweep/run-1",
    )
    args = parser.parse_args()
    output_dir = args.run_dir / "plots"
    output_dir.mkdir(parents=True, exist_ok=True)
    plot_latency(args.run_dir, output_dir)
    plot_cpu(args.run_dir, output_dir)
    print(f"Saved both plots to {output_dir}")


if __name__ == "__main__":
    main()
