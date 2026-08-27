# gpubuild

Compile CUDA farm binaries anywhere podman runs — **no CUDA install, no GPU,
no NVIDIA driver on the build host** — and ship only compiled binaries to
cloud GPUs, certifying results by checksum instead of trusting the host.

Full documentation: open [`index.html`](index.html) in a browser.

```
./gpu-cc.sh -O2 -arch=sm_75 -o hello hello.cu     # nvcc in a pinned container
./build_farm.sh model.c harness.cu farm            # gsm model -> fat binary
./ship_run.sh user@host 'cd ~ && ./farm ...' farm  # run on any ssh GPU host
VAST_API_KEY=... ./vast_run.sh <id> 'cmd' farm     # binary-only vast.ai session
```

The scripts are self-contained; nothing else in this repository is required.
