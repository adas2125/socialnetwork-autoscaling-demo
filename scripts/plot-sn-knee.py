import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import re
import matplotlib.pyplot as plt

def milliseconds(value):
    number, unit = re.fullmatch(r"([\d.]+)(ns|us|µs|ms|s|m)", value).groups()
    assert unit == "ms", f"Expected milliseconds, got {unit}"
    return float(number)

def plot_latency(run_dir, output_dir):
    rates = [150, 300, 600, 1200, 1400]
    latencies = []
    for rate in rates:
        # hard-coded for plotting
        if rate == 1400:
            path = run_dir / f"cpu-{rate}"/"wrk2.txt"
        else:
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
        # print(f"path: {path}, values: {values}")
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
    # print(f"Reading CPU samples from {path}")
    samples = []
    timestamp = None
    services = defaultdict(float)
    pod_section = False
    for line in path.read_text().splitlines():
        # print(line)
        line = line.strip()
        # match timestamp line
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", line):
            if timestamp is not None and services:
                samples.append((timestamp, dict(services)))
            timestamp = datetime.strptime(line, "%Y-%m-%dT%H:%M:%SZ")
            services = defaultdict(float)
            pod_section = False
        elif line.startswith("NAME"):
            # Node tables contain CPU(%); pod tables do not.
            pod_section = "CPU(cores)" in line and "CPU(%)" not in line
        
        # matching pod CPU lines
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

def read_worker_cpu(path):
    samples = []
    timestamp = None
    nodes = {}
    node_section = False
    for line in path.read_text().splitlines():
        line = line.strip()
        # match timestamp line
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", line):
            if timestamp is not None and nodes:
                samples.append((timestamp, nodes))
            timestamp = datetime.strptime(line, "%Y-%m-%dT%H:%M:%SZ")
            nodes = {}
            node_section = False
        elif line.startswith("NAME"):
            # match node CPU table header
            node_section = "CPU(cores)" in line and "CPU(%)" in line
        elif node_section and timestamp is not None:
            fields = line.split()
            # print(fields)
            if len(fields) != 5:
                continue
            node, cpu_percent = fields[0], fields[2]
            nodes[node] = float(cpu_percent.rstrip("%"))
    if timestamp is not None and nodes:
        samples.append((timestamp, nodes))
    if not samples:
        raise ValueError(f"No node CPU samples found in {path}")
    return samples


def plot_cpu(run_dir, output_dir, label, type="service"):
    if type == "service":
        samples = read_cpu(run_dir / label / "cpu.txt")
    elif type == "worker":
        samples = read_worker_cpu(run_dir / label / "cpu.txt")
    else:
        raise ValueError(f"Unknown type: {type}")
    # converting to relative time in seconds since the first sample
    elapsed = [(timestamp - samples[0][0]).total_seconds()
               for timestamp, _ in samples]
    if type == "service":
        entities = sorted({name for _, values in samples for name in values})
        ylabel = "CPU cores (sum across replicas)"
        title = f"Service CPU over time — {label}"
        filename = f"service-cpu-{label}.png"
    else:
        entities = sorted({name for _, values in samples for name in values})
        ylabel = "CPU utilization (%)"
        title = f"Node CPU over time — {label}"
        filename = f"worker-cpu-{label}.png"
    # print(entities)
    # print(samples)

    fig, ax = plt.subplots(figsize=(14, 7))
    colors = plt.get_cmap("tab20")
    for i, entity in enumerate(entities):
        # obtaining CPU values for the current entity
        values = [cpu.get(entity, float("nan")) for _, cpu in samples]
        ax.plot(elapsed, values, label=entity, color=colors(i % 20),
                linestyle="-" if i < 20 else "--", linewidth=1.3)
    ax.set_xlabel("Seconds since CPU logging began")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    if type == "service":
        ax.set_ylim(bottom=0)
    else:
        ax.set_ylim(0, 100)
    ax.grid(alpha=0.25)
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), fontsize=8)
    fig.tight_layout()
    fig.savefig(output_dir / filename, dpi=150)
    plt.close(fig)



if __name__ == "__main__":
    RUN_DIR = Path("results/sn-knee/standalone-sweep/run-1/")
    output_dir = Path("plots")
    output_dir.mkdir(parents=True, exist_ok=True)
    plot_latency(RUN_DIR, output_dir)
    plot_cpu(RUN_DIR, output_dir, "cpu-1200", type="service")
    plot_cpu(RUN_DIR, output_dir, "cpu-1400", type="service")
    plot_cpu(RUN_DIR, output_dir, "cpu-1600", type="service")

    plot_cpu(RUN_DIR, output_dir, "cpu-1200", type="worker")
    plot_cpu(RUN_DIR, output_dir, "cpu-1400", type="worker")
    plot_cpu(RUN_DIR, output_dir, "cpu-1600", type="worker")

    print(f"Saved both plots to {output_dir}")
