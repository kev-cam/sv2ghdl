Building the Cadnip-enabled VACASK for the engine oracle (docs/VAMOS_SPECTRE_DESIGN.md §11 T3b and
§0 "Sources"; the gate `needs_cadnip` in tests/vamos/spectre_testlib.py reads VAMOS_CADNIP_VACASK and
SIM_MODULE_PATH from the environment and nothing else).

What it is
----------
VACASK configured with -DCADNIP_PARSERS=ON reads a Spectre-language file named by
`include "<file>" lang=spectre` through NetlistParse.rs (the Rust crate netlist-cxx, built by cargo
through Corrosion and wired into the vacask binary; VACASK calls it the "netlistrs adapter").  The
oracle of §11 T3b strips line 1 (the title) of each cadnip/<deck>.scs and wraps the body in a native
VACASK deck, so the Spectre text is parsed by a second, independent implementation and simulated by
the same engine vamos drives; expect.json holds the values that run gave.

The build the design's experiments used (§0 Sources "the Cadnip evaluation build", §13 E76)
--------------------------------------------------------------------------------------------
  source      https://github.com/kev-cam/VACASK (a fork of https://codeberg.org/arpadbuermen/VACASK)
              at 0.3.4-94-gcdf13d22, i.e. cdf13d22f96747b5086ab236e805f980fc9eb488 (2026-10-01)
  parser      NetlistParse.rs d565fd3e359893fbc4376bb9c7b5608ef786e6bb, the commit VACASK's
              CMakeLists.txt pins when NETLIST_RS_DIR is unset (the build passed a local checkout at
              that commit through -DNETLIST_RS_DIR); the same commit the cst/ corpus comes from
  toolchain   gcc 15.2 (Ubuntu), CMake 3.31.4, Ninja 1.13.2, Rust stable through rustup (cargo
              1.97.0), Corrosion v0.5.1 (CMake fetches it), OpenBLAS, openvaf-r 20260616
  options     -G Ninja -DCMAKE_BUILD_TYPE=Release -DCADNIP_PARSERS=ON -DVACASK_CINTERFACE=ON
              -DOPENVAF_DIR=/opt/openvaf-r-20260616 -DNETLIST_RS_DIR=<NetlistParse.rs checkout>
  result      <build>/simulator/vacask, which reports "This is vacask 0.3.4-94-gcdf13d22."; that
              build directory holds no devices/ (its OSDI libraries were not built), so the runs
              set SIM_MODULE_PATH to the regular build's /opt/build.VACASK/Release/devices
              (resistor.osdi, capacitor.osdi, inductor.osdi, ... of VACASK 0.3.4-91-g64489cf7)

Recipe (WSL; cargo must be on the PATH, `. ~/.cargo/env`)
--------------------------------------------------------
  git clone https://github.com/kev-cam/VACASK vacask-src
  git -C vacask-src checkout cdf13d22f96747b5086ab236e805f980fc9eb488
  git clone https://github.com/NyanCAD/NetlistParse.rs nprs
  git -C nprs checkout d565fd3e359893fbc4376bb9c7b5608ef786e6bb
  cmake -S vacask-src -B vacask-cadnip -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DCADNIP_PARSERS=ON -DVACASK_CINTERFACE=ON \
        -DNETLIST_RS_DIR=$PWD/nprs -DOPENVAF_DIR=/opt/openvaf-r-20260616
  cmake --build vacask-cadnip
  vacask-cadnip/simulator/vacask -v          # "This is vacask 0.3.4-94-gcdf13d22."

Without -DNETLIST_RS_DIR, CMake fetches NetlistParse.rs at the pinned commit itself (network needed);
without a Rust toolchain the configure step stops with "CADNIP_PARSERS=ON requires a Rust toolchain".

Running the oracle tier
-----------------------
  export VAMOS_CADNIP_VACASK=$PWD/vacask-cadnip/simulator/vacask
  export SIM_MODULE_PATH=/opt/build.VACASK/Release/devices   # or vacask-cadnip/devices once it holds the .osdi files
  cd tests/vamos && python3 -m unittest test_spectre_cadnip     # phase 2's T3b test (§12); skipped when unset

By hand, what the harness does for one deck (§11 T3b steps 2-5):
  tail -n +2 cadnip/pilot_inline_if.scs > body.scs
  printf 'cadnip oracle\nground 0\nmodel resistor resistor\nmodel vsource vsource\n' > top.sim
  printf 'include "body.scs" lang=spectre\ncontrol\n  options rawfile="ascii"\n  analysis op1 op\nendc\n' >> top.sim
  $VAMOS_CADNIP_VACASK -qp top.sim
The run prints "WARNING: netlistrs adapter ignoring 1 analysis command(s) from an included
foreign-format file": the deck's own `dcop dc` is replaced by the control block, as intended.  op1.raw
then holds the columns expect.json lists for that deck (in, n1, n2, v1:flow(br), x3:m).

The committed cadnip/<deck>.cst dumps are not made by this build but by cst/regen.sh (spectre_dump
over the netlist-syntax crate at the same NetlistParse.rs commit, on the deck without its title line):
T0 checks that each dump tiles its deck with no Error node, so the oracle never runs on a deck whose
statements the parser dropped silently (§13 E76).
