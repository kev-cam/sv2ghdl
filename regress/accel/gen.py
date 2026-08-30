#!/usr/bin/env python3
"""
accelbench generator -- matched VHDL / SystemVerilog benchmark designs for
nvc --accel vs nvc-interp vs Verilator.

Every design obeys the --accel admission rules (measured, nvc @ f251009c0):
  * >= 8 SCOPE_INSTANCE nodes AND >= 8 distinct (entity,generics) variants
    in the chunk subtree  -> so it installs at the DEFAULT gate, no
    NVC_ACCEL_MIN_MODULES knob.
  * direct `entity work.X` instantiation only (component decls decline).
    NOTE: `for ... generate` ALSO declines (vhdl2vlog.c:1843, /*?block k=60*/),
    which is why the structural repetition is emitted here rather than
    written as a generate loop.
  * std_logic / std_logic_vector ports only.
  * every flop is `if rising_edge(clk)` with a reset-priority sync reset.
  * no assert/report/pcall anywhere in a DUT that is meant to install
    (the sole exception is `many`, where the top's assert is the *mechanism*
    that fragments the design into many chunks).

Both language versions are emitted from the same source of truth so the two
engines run the identical workload.  The testbench is identical in structure:
32-bit LFSR stimulus, 4 reset cycles, 32-bit rotate-xor checksum, and a
"Y=<int>" line carrying chk[30:0].

usage:  gen.py <shape> <outdir> [K=V ...]
"""
import os, sys, math

# ----------------------------------------------------------------- helpers
def T(s, **kw):
    for k, v in kw.items():
        s = s.replace("@%s@" % k, str(v))
    return s

def wr(d, name, text):
    with open(os.path.join(d, name), "w") as f:
        f.write(text)

VHDR = ("library ieee;\n"
        "use ieee.std_logic_1164.all;\n"
        "use ieee.numeric_std.all;\n\n")

def hexlit(v, w):
    """VHDL bit-string literal for a w-bit constant."""
    if w % 4 == 0:
        return 'x"%0*X"' % (w // 4, v & ((1 << w) - 1))
    return '"%s"' % format(v & ((1 << w) - 1), "0%db" % w)

# ----------------------------------------------------------------- TB
VHDL_TB = VHDR + """entity @S@_tb is
  generic (CYC : positive := @CYC@@EXTRAG@);
end entity;

architecture sim of @S@_tb is
  signal clk  : std_logic := '0';
  signal rst  : std_logic := '1';
  signal d    : std_logic_vector(31 downto 0) := (others => '0');
  signal q    : std_logic_vector(31 downto 0);
  signal lfsr : std_logic_vector(31 downto 0) := x"ACE1BEEF";
  signal chk  : unsigned(31 downto 0) := x"00000001";
  signal cnt  : unsigned(31 downto 0) := (others => '0');
  signal run  : boolean := true;
@EXTRASIG@begin
  dut : entity work.@S@_top port map (clk => clk, rst => rst, d => d, q => q@EXTRAMAP@);

  clk <= not clk after 5 ns when run else '0';
@RSTDRV@
  d   <= lfsr;
@EXTRADRV@
  drv : process (clk) is
  begin
    if rising_edge(clk) then
      lfsr <= lfsr(30 downto 0) & (lfsr(31) xor lfsr(21) xor lfsr(1) xor lfsr(0));
      cnt  <= cnt + 1;
      if cnt < 4 then
        chk <= x"00000001";
      else
        chk <= (chk(30 downto 0) & chk(31)) xor unsigned(q);
      end if;
    end if;
  end process;

  fin : process is
  begin
    for i in 1 to CYC loop
      wait until rising_edge(clk);
    end loop;
    wait for 1 ns;
    report "Y=" & integer'image(to_integer(chk(30 downto 0)));
    run <= false;
    wait;
  end process;
end architecture;
"""

SV_TB = """
module @S@_tb #(parameter int CYC = @CYC@@EXTRAP@) ();
  logic        clk  = 1'b0;
  logic [31:0] lfsr = 32'hACE1BEEF;
  logic [31:0] chk  = 32'h00000001;
  logic [31:0] cnt  = 32'h0;
  wire  [31:0] d    = lfsr;
@SVRST@
  wire  [31:0] q;
@SVEXTRA@
  @S@_top dut (.clk(clk), .rst(rst), .d(d), .q(q)@SVMAP@);

  always #5 clk = ~clk;

  always @(posedge clk) begin
    lfsr <= {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
    cnt  <= cnt + 32'd1;
    if (cnt < 32'd4) chk <= 32'h00000001;
    else             chk <= {chk[30:0], chk[31]} ^ q;
  end

  initial begin
    repeat (CYC) @(posedge clk);
    #1;
    $display("Y=%0d", chk[30:0]);
    $finish;
  end
endmodule
"""

def emit_tb(d, S, CYC, extra=None):
    e = extra or {}
    rstdrv = e.get("rstdrv", "  rst <= '1' when cnt < 4 else '0';")
    svrst = e.get("svrst", "  wire         rst  = (cnt < 32'd4);")
    wr(d, "%s_tb.vhd" % S, T(VHDL_TB, S=S, CYC=CYC,
                             EXTRAG=e.get("vg", ""), EXTRASIG=e.get("vsig", ""),
                             EXTRAMAP=e.get("vmap", ""), EXTRADRV=e.get("vdrv", ""),
                             RSTDRV=rstdrv))
    return T(SV_TB, S=S, CYC=CYC, EXTRAP=e.get("sp", ""),
             SVEXTRA=e.get("ssig", ""), SVMAP=e.get("smap", ""),
             SVRST=svrst)

# ================================================================== WIDE
def gen_wide(d, p):
    N, W, CYC = p.get("N", 8), p.get("W", 1024), p.get("CYC", 2000)
    assert W % 32 == 0 and W >= 64 and N >= 8
    L = W // 32
    S = "wide"

    wr(d, "wide_stage.vhd", VHDR + T("""entity wide_stage is
  generic (ID : natural := 0; W : positive := 256);
  port (clk, rst : in  std_logic;
        d        : in  std_logic_vector(W-1 downto 0);
        q        : out std_logic_vector(W-1 downto 0));
end entity;

architecture rtl of wide_stage is
  constant R  : natural := 1 + ((ID*13 + 7) mod (W-1));
  constant K  : natural := (ID*40503 + 12345) mod 65536;
  signal t1, t2, t3, nxt : std_logic_vector(W-1 downto 0);
  signal gt   : std_logic;
begin
  t1  <= std_logic_vector(unsigned(d) + to_unsigned(K, W));
  t2  <= d(W-1-R downto 0) & d(W-1 downto W-R);
  t3  <= t1 xor t2;
  gt  <= '1' when unsigned(t3) > unsigned(d) else '0';
  nxt <= t3 when gt = '1' else std_logic_vector(unsigned(t3) - to_unsigned(K, W));

  reg : process (clk) is
  begin
    if rising_edge(clk) then
      if rst = '1' then q <= (others => '0');
      else              q <= nxt;
      end if;
    end if;
  end process;
end architecture;
"""))

    sigs = "\n".join("  signal s%d : std_logic_vector(%d downto 0);" % (i, W - 1)
                     for i in range(N + 1))
    exp = " &\n        ".join("(d xor %s)" % hexlit((i * 0x9E3779B9) & 0xFFFFFFFF, 32)
                              for i in range(L))
    insts = "\n".join(
        "  u%d : entity work.wide_stage generic map (ID => %d, W => %d)\n"
        "    port map (clk => clk, rst => rst, d => s%d, q => s%d);" % (i, i, W, i, i + 1)
        for i in range(N))
    fold = " xor\n        ".join("s%d(%d downto %d)" % (N, 32 * (i + 1) - 1, 32 * i)
                                 for i in range(L))
    wr(d, "wide_top.vhd", VHDR + T("""entity wide_top is
  port (clk, rst : in  std_logic;
        d        : in  std_logic_vector(31 downto 0);
        q        : out std_logic_vector(31 downto 0));
end entity;

architecture rtl of wide_top is
@SIGS@
  signal fo : std_logic_vector(31 downto 0);
begin
  s0 <= @EXP@;

@INSTS@

  fo <= @FOLD@;

  oreg : process (clk) is
  begin
    if rising_edge(clk) then
      if rst = '1' then q <= (others => '0');
      else              q <= fo;
      end if;
    end if;
  end process;
end architecture;
""", SIGS=sigs, EXP=exp, INSTS=insts, FOLD=fold))

    sv_stage = T("""
module wide_stage #(parameter int ID = 0, parameter int W = 256)
  (input  logic         clk,
   input  logic         rst,
   input  logic [W-1:0] d,
   output logic [W-1:0] q);
  localparam int         R = 1 + ((ID*13 + 7) % (W-1));
  localparam logic [W-1:0] K = (ID*40503 + 12345) % 65536;
  wire [W-1:0] t1  = d + K;
  wire [W-1:0] t2  = {d[W-1-R:0], d[W-1:W-R]};
  wire [W-1:0] t3  = t1 ^ t2;
  wire         gt  = (t3 > d);
  wire [W-1:0] nxt = gt ? t3 : (t3 - K);
  always @(posedge clk) begin
    if (rst) q <= '0;
    else     q <= nxt;
  end
endmodule
""")
    sv_sigs = "\n".join("  wire [%d:0] s%d;" % (W - 1, i) for i in range(N + 1))
    sv_exp = ",\n                 ".join("(d ^ 32'h%08X)" % ((i * 0x9E3779B9) & 0xFFFFFFFF)
                                         for i in range(L))
    sv_insts = "\n".join(
        "  wide_stage #(.ID(%d), .W(%d)) u%d (.clk(clk), .rst(rst), .d(s%d), .q(s%d));"
        % (i, W, i, i, i + 1) for i in range(N))
    sv_fold = " ^\n                  ".join("s%d[%d:%d]" % (N, 32 * (i + 1) - 1, 32 * i)
                                            for i in range(L))
    sv_top = T("""
module wide_top (input logic clk, input logic rst,
                 input logic [31:0] d, output logic [31:0] q);
@SIGS@
  assign s0 = {@EXP@};
@INSTS@
  wire [31:0] fo = @FOLD@;
  always @(posedge clk) begin
    if (rst) q <= 32'h0;
    else     q <= fo;
  end
endmodule
""", SIGS=sv_sigs, EXP=sv_exp, INSTS=sv_insts, FOLD=sv_fold)

    sv_tb = emit_tb(d, S, CYC)
    wr(d, "wide.sv", sv_stage + sv_top + sv_tb)
    return dict(top="wide_top", tb="wide_tb", sv="wide.sv", cyc=CYC,
                desc="WIDE datapath: %d stages x %d bits" % (N, W))

# ================================================================== FSM
# NOTE ON SHAPE: the interconnect is a 4-bit VECTOR in a linear CHAIN, not
# scalar std_logic in a parallel fan-out.  Measured (bug_fsm/): under
# NVC_ACCEL_PER_INSTANCE=1 a parallel fan-out of chunks gives the wrong answer
# (chain: correct).  Whole-subtree accel is correct for both.  The design is
# still the NARROW/CONTROL diagnostic: 4-bit state, 16-way case, 4-bit
# interconnect -- versus `wide`'s 1024-bit arithmetic.
FSM_CASE_V = """    case st is
      when x"0" => if iv(0) = \'1\' then nx <= x"1"; else nx <= x"8"; end if;
      when x"1" => if iv(3) = \'1\' then nx <= x"3"; else nx <= x"0"; end if;
      when x"2" => nx <= std_logic_vector(unsigned(st) + A1);
      when x"3" => if iv(0) = \'1\' then nx <= x"7"; else nx <= x"2"; end if;
      when x"4" => if iv(3) = \'1\' then nx <= x"C"; else nx <= x"5"; end if;
      when x"5" => nx <= std_logic_vector(unsigned(st) xor B1);
      when x"6" => if iv(1) = \'1\' then nx <= x"E"; else nx <= x"4"; end if;
      when x"7" => nx <= std_logic_vector(unsigned(st) - A1);
      when x"8" => if iv(2) = \'1\' then nx <= x"9"; else nx <= x"F"; end if;
      when x"9" => nx <= std_logic_vector(unsigned(st) + B1);
      when x"A" => if iv(0) = \'1\' then nx <= x"6"; else nx <= x"B"; end if;
      when x"B" => nx <= x"D";
      when x"C" => if iv(3) = \'1\' then nx <= x"A"; else nx <= x"0"; end if;
      when x"D" => nx <= std_logic_vector(unsigned(st) + A1 + B1);
      when x"E" => if iv(1) = \'1\' then nx <= x"1"; else nx <= x"C"; end if;
      when others => nx <= x"4";
    end case;
"""

FSM_CASE_S = """    case (st)
      4\'h0: nx = iv[0] ? 4\'h1 : 4\'h8;
      4\'h1: nx = iv[3] ? 4\'h3 : 4\'h0;
      4\'h2: nx = st + A1;
      4\'h3: nx = iv[0] ? 4\'h7 : 4\'h2;
      4\'h4: nx = iv[3] ? 4\'hC : 4\'h5;
      4\'h5: nx = st ^ B1;
      4\'h6: nx = iv[1] ? 4\'hE : 4\'h4;
      4\'h7: nx = st - A1;
      4\'h8: nx = iv[2] ? 4\'h9 : 4\'hF;
      4\'h9: nx = st + B1;
      4\'hA: nx = iv[0] ? 4\'h6 : 4\'hB;
      4\'hB: nx = 4\'hD;
      4\'hC: nx = iv[3] ? 4\'hA : 4\'h0;
      4\'hD: nx = st + A1 + B1;
      4\'hE: nx = iv[1] ? 4\'h1 : 4\'hC;
      default: nx = 4\'h4;
    endcase
"""

FSM_STAGE_V = VHDR + T("""entity @NAME@ is
  generic (ID : natural := 0);
  port (clk, rst : in  std_logic;
        iv       : in  std_logic_vector(3 downto 0);
        ov       : out std_logic_vector(3 downto 0));
end entity;

architecture rtl of @NAME@ is
  constant A1 : unsigned(3 downto 0) := to_unsigned(1 + (ID mod 3), 4);
  constant B1 : unsigned(3 downto 0) := to_unsigned(1 + (ID mod 7), 4);
  constant MY : std_logic_vector(3 downto 0) :=
    std_logic_vector(to_unsigned(ID mod 16, 4));
  signal st : std_logic_vector(3 downto 0);
  signal nx : std_logic_vector(3 downto 0);
begin
  comb : process (st, iv) is
  begin
@CASE@  end process;

  reg : process (clk) is
  begin
    if rising_edge(clk) then
      if rst = \'1\' then
        st <= x"0";
        ov <= x"0";
      else
        st <= nx;
        if nx = MY then ov <= nx xor iv;
        else            ov <= (nx xor iv) xor x"5";
        end if;
      end if;
    end if;
  end process;
end architecture;
""", CASE=FSM_CASE_V)

FSM_STAGE_S = T("""
module @NAME@ #(parameter int ID = 0)
  (input logic clk, input logic rst,
   input  logic [3:0] iv,
   output logic [3:0] ov);
  localparam logic [3:0] A1 = 1 + (ID % 3);
  localparam logic [3:0] B1 = 1 + (ID % 7);
  localparam logic [3:0] MY = ID % 16;
  logic [3:0] st;
  logic [3:0] nx;
  always @(*) begin
@CASE@  end
  always @(posedge clk) begin
    if (rst) begin
      st <= 4\'h0; ov <= 4\'h0;
    end else begin
      st <= nx;
      if (nx == MY) ov <= nx ^ iv;
      else          ov <= (nx ^ iv) ^ 4\'h5;
    end
  end
endmodule
""", CASE=FSM_CASE_S)


def fsm_chain(prefix, N, idexpr=lambda i: str(i)):
    """Linear chain of N stages, 4-bit vector interconnect."""
    vs = ["  signal iv0 : std_logic_vector(3 downto 0);"]
    ss = ["  wire [3:0] iv0;"]
    vi, si = [], []
    for i in range(N):
        vs.append("  signal v%d : std_logic_vector(3 downto 0);" % i)
        ss.append("  wire [3:0] v%d;" % i)
        src = "iv0" if i == 0 else "v%d" % (i - 1)
        vi.append("  u%d : entity work.%s generic map (ID => %s)\n"
                  "    port map (clk => clk, rst => rst, iv => %s, ov => v%d);"
                  % (i, prefix, idexpr(i), src, i))
        si.append("  %s #(.ID(%s)) u%d (.clk(clk), .rst(rst), .iv(%s), .ov(v%d));"
                  % (prefix, idexpr(i), i, src, i))
    return "\n".join(vs), "\n".join(vi), "\n".join(ss), "\n".join(si)


def fsm_collect(N):
    """32 bits = 8 nibbles; nibble k = xor of every stage with i mod 8 == k."""
    buckets = [[] for _ in range(8)]
    for i in range(N):
        buckets[i % 8].append(i)
    v, s = [], []
    for k in range(8):
        b = buckets[k] or [0]
        v.append("(" + " xor ".join("v%d" % i for i in b) + ")")
        s.append("(" + " ^ ".join("v%d" % i for i in b) + ")")
    return " & ".join(v), "{" + ", ".join(s) + "}"


def gen_fsm(d, p):
    N, CYC = p.get("N", 8), p.get("CYC", 2000)
    assert N >= 8
    S = "fsm"
    wr(d, "fsm_stage.vhd", T(FSM_STAGE_V, NAME="fsm_stage"))
    vs, vi, ss, si = fsm_chain("fsm_stage", N)
    vq, sq = fsm_collect(N)

    wr(d, "fsm_top.vhd", VHDR + T("""entity fsm_top is
  port (clk, rst : in  std_logic;
        d        : in  std_logic_vector(31 downto 0);
        q        : out std_logic_vector(31 downto 0));
end entity;

architecture rtl of fsm_top is
@SIGS@
  signal acc : std_logic_vector(31 downto 0);
begin
  iv0 <= d(3 downto 0) xor d(7 downto 4) xor d(31 downto 28);

@INSTS@

  acc <= @Q@;

  oreg : process (clk) is
  begin
    if rising_edge(clk) then
      if rst = \'1\' then q <= (others => \'0\');
      else              q <= acc;
      end if;
    end if;
  end process;
end architecture;
""", SIGS=vs, INSTS=vi, Q=vq))

    sv_top = T("""
module fsm_top (input logic clk, input logic rst,
                input logic [31:0] d, output logic [31:0] q);
@SIGS@
  assign iv0 = d[3:0] ^ d[7:4] ^ d[31:28];
@INSTS@
  wire [31:0] acc = @Q@;
  always @(posedge clk) begin
    if (rst) q <= 32\'h0;
    else     q <= acc;
  end
endmodule
""", SIGS=ss, INSTS=si, Q=sq)

    sv_tb = emit_tb(d, S, CYC)
    wr(d, "fsm.sv", T(FSM_STAGE_S, NAME="fsm_stage") + sv_top + sv_tb)
    return dict(top="fsm_top", tb="fsm_tb", sv="fsm.sv", cyc=CYC,
                desc="NARROW/control: chain of %d x 16-state FSM, 4-bit interconnect" % N)

# ================================================================== DEEP
def gen_deep(d, p):
    D, W, CYC = p.get("D", 32), p.get("W", 32), p.get("CYC", 2000)
    assert D >= 8 and W >= 32
    S = "deep"
    wr(d, "deep_stage.vhd", VHDR + """entity deep_stage is
  generic (ID : natural := 0; W : positive := 32);
  port (a : in  std_logic_vector(W-1 downto 0);
        y : out std_logic_vector(W-1 downto 0));
end entity;

architecture rtl of deep_stage is
  constant R  : natural := 1 + ((ID*5 + 3) mod (W-1));
  constant K  : natural := (ID*2654 + 991) mod 65536;
  constant K2 : natural := (ID*7919 + 13) mod 65536;
  constant BI : natural := (ID*7 + 3) mod W;
  signal t : std_logic_vector(W-1 downto 0);
begin
  t <= std_logic_vector(unsigned(a) + to_unsigned(K, W));
  y <= (t(W-1-R downto 0) & t(W-1 downto W-R)) when t(BI) = '1'
       else (t xor std_logic_vector(to_unsigned(K2, W)));
end architecture;
""")
    sigs = "\n".join("  signal c%d : std_logic_vector(%d downto 0);" % (i, W - 1)
                     for i in range(D + 1))
    insts = "\n".join(
        "  u%d : entity work.deep_stage generic map (ID => %d, W => %d)\n"
        "    port map (a => c%d, y => c%d);" % (i, i, W, i, i + 1) for i in range(D))
    wr(d, "deep_top.vhd", VHDR + T("""entity deep_top is
  port (clk, rst : in  std_logic;
        d        : in  std_logic_vector(31 downto 0);
        q        : out std_logic_vector(31 downto 0));
end entity;

architecture rtl of deep_top is
  signal ir : std_logic_vector(31 downto 0);
@SIGS@
begin
  ireg : process (clk) is
  begin
    if rising_edge(clk) then
      if rst = '1' then ir <= (others => '0');
      else              ir <= d xor q;
      end if;
    end if;
  end process;

  c0 <= ir;

@INSTS@

  oreg : process (clk) is
  begin
    if rising_edge(clk) then
      if rst = '1' then q <= (others => '0');
      else              q <= c@D@;
      end if;
    end if;
  end process;
end architecture;
""", SIGS=sigs, INSTS=insts, D=D))

    sv_stage = """
module deep_stage #(parameter int ID = 0, parameter int W = 32)
  (input logic [W-1:0] a, output logic [W-1:0] y);
  localparam int           R  = 1 + ((ID*5 + 3) % (W-1));
  localparam logic [W-1:0] K  = (ID*2654 + 991) % 65536;
  localparam logic [W-1:0] K2 = (ID*7919 + 13) % 65536;
  localparam int           BI = (ID*7 + 3) % W;
  wire [W-1:0] t = a + K;
  assign y = t[BI] ? {t[W-1-R:0], t[W-1:W-R]} : (t ^ K2);
endmodule
"""
    sv_sigs = "\n".join("  wire [%d:0] c%d;" % (W - 1, i) for i in range(D + 1))
    sv_insts = "\n".join(
        "  deep_stage #(.ID(%d), .W(%d)) u%d (.a(c%d), .y(c%d));" % (i, W, i, i, i + 1)
        for i in range(D))
    sv_top = T("""
module deep_top (input logic clk, input logic rst,
                 input logic [31:0] d, output logic [31:0] q);
  logic [31:0] ir;
@SIGS@
  always @(posedge clk) begin
    if (rst) ir <= 32'h0;
    else     ir <= d ^ q;
  end
  assign c0 = ir;
@INSTS@
  always @(posedge clk) begin
    if (rst) q <= 32'h0;
    else     q <= c@D@;
  end
endmodule
""", SIGS=sv_sigs, INSTS=sv_insts, D=D)
    sv_tb = emit_tb(d, S, CYC)
    wr(d, "deep.sv", sv_stage + sv_top + sv_tb)
    return dict(top="deep_top", tb="deep_tb", sv="deep.sv", cyc=CYC,
                desc="DEEP comb: %d chained pure-comb stages x %d bits" % (D, W))

# ================================================================== REGF
def gen_regf(d, p):
    N, DEPTH, W, CYC = p.get("N", 8), p.get("DEPTH", 16), p.get("W", 32), p.get("CYC", 2000)
    assert N >= 8 and DEPTH in (8, 16, 32, 64) and W == 32
    S = "regf"
    A = int(math.log2(DEPTH))
    wsel = "\n".join(
        '          when %s => regs(%d downto %d) <= wd xor %s;'
        % (hexlit(i, A), W * (i + 1) - 1, W * i, hexlit((i * 0x01000193) & 0xFFFFFFFF, 32))
        for i in range(DEPTH))
    rsel = "\n".join(
        '          when %s => rd <= regs(%d downto %d);' % (hexlit(i, A), W * (i + 1) - 1, W * i)
        for i in range(DEPTH - 1))
    rsel += '\n          when others => rd <= regs(%d downto %d);' % (W * DEPTH - 1, W * (DEPTH - 1))
    wr(d, "regf_bank.vhd", VHDR + T("""entity regf_bank is
  generic (ID : natural := 0);
  port (clk, rst, we : in  std_logic;
        wa           : in  std_logic_vector(@AM@ downto 0);
        wd           : in  std_logic_vector(@WM@ downto 0);
        ra           : in  std_logic_vector(@AM@ downto 0);
        rd           : out std_logic_vector(@WM@ downto 0));
end entity;

architecture rtl of regf_bank is
  constant TAG : std_logic_vector(@WM@ downto 0) :=
    std_logic_vector(to_unsigned((ID*2654435 + 7) mod 65536, @W@));
  signal regs : std_logic_vector(@RM@ downto 0);
begin
  rf : process (clk) is
  begin
    if rising_edge(clk) then
      if rst = '1' then
        regs <= (others => '0');
        rd   <= (others => '0');
      else
        if we = '1' then
          case wa is
@WSEL@
            when others => null;
          end case;
        end if;
        case ra is
@RSEL@
        end case;
      end if;
    end if;
  end process;
end architecture;
""", AM=A - 1, WM=W - 1, W=W, RM=W * DEPTH - 1, WSEL=wsel, RSEL=rsel))

    # top: N banks, per-bank address skew, rotate-xor collect (no cancellation)
    sigs, insts, terms = [], [], []
    for i in range(N):
        # NOTE: the per-bank address skew is a NAMED SIGNAL, not a port-map
        # expression.  `port map (ra => rav xor x"1")` builds an <inst>.RA_actual
        # conversion function whose result buffer is collected after ~2-5k
        # evaluations -> SIGSEGV in ieee_xor_vector_sse41 in the INTERPRETER.
        # See BUG_portmap_xor_sigsegv.vhd.  Unrelated to --accel.
        sigs.append("  signal rd%d : std_logic_vector(%d downto 0);" % (i, W - 1))
        sigs.append("  signal wa%d, ra%d : std_logic_vector(%d downto 0);" % (i, i, A - 1))
        insts.append(
            "  wa%d <= wav xor %s;\n"
            "  ra%d <= rav xor %s;\n"
            "  u%d : entity work.regf_bank generic map (ID => %d)\n"
            "    port map (clk => clk, rst => rst, we => wev,\n"
            "              wa => wa%d, wd => d, ra => ra%d, rd => rd%d);"
            % (i, hexlit(i % DEPTH, A), i, hexlit((i * 3 + 1) % DEPTH, A), i, i, i, i, i))
        r = i % 32
        if r == 0:
            terms.append("rd%d" % i)
        else:
            terms.append("(rd%d(%d downto 0) & rd%d(%d downto %d))" % (i, W - 1 - r, i, W - 1, W - r))
    wr(d, "regf_top.vhd", VHDR + T("""entity regf_top is
  port (clk, rst : in  std_logic;
        d        : in  std_logic_vector(31 downto 0);
        q        : out std_logic_vector(31 downto 0));
end entity;

architecture rtl of regf_top is
  signal wev : std_logic;
  signal wav : std_logic_vector(@AM@ downto 0);
  signal rav : std_logic_vector(@AM@ downto 0);
@SIGS@
  signal acc : std_logic_vector(31 downto 0);
begin
  wev <= d(31) xor d(0);
  wav <= d(@AM@ downto 0);
  rav <= d(@AH@ downto @A@);

@INSTS@

  acc <= @TERMS@;

  oreg : process (clk) is
  begin
    if rising_edge(clk) then
      if rst = '1' then q <= (others => '0');
      else              q <= acc;
      end if;
    end if;
  end process;
end architecture;
""", AM=A - 1, A=A, AH=2 * A - 1, SIGS="\n".join(sigs), INSTS="\n".join(insts),
     TERMS=" xor\n         ".join(terms)))

    sv_wsel = "\n".join("            %d'h%X: regs[%d:%d] <= wd ^ 32'h%08X;"
                        % (A, i, W * (i + 1) - 1, W * i, (i * 0x01000193) & 0xFFFFFFFF)
                        for i in range(DEPTH))
    sv_rsel = "\n".join("          %d'h%X: rd <= regs[%d:%d];" % (A, i, W * (i + 1) - 1, W * i)
                        for i in range(DEPTH - 1))
    sv_rsel += "\n          default: rd <= regs[%d:%d];" % (W * DEPTH - 1, W * (DEPTH - 1))
    sv_bank = T("""
module regf_bank #(parameter int ID = 0)
  (input logic clk, input logic rst, input logic we,
   input logic [@AM@:0] wa, input logic [@WM@:0] wd,
   input logic [@AM@:0] ra, output logic [@WM@:0] rd);
  logic [@RM@:0] regs;
  always @(posedge clk) begin
    if (rst) begin
      regs <= '0;
      rd   <= '0;
    end else begin
      if (we) begin
        case (wa)
@WSEL@
          default: ;
        endcase
      end
      case (ra)
@RSEL@
      endcase
    end
  end
endmodule
""", AM=A - 1, WM=W - 1, RM=W * DEPTH - 1, WSEL=sv_wsel, RSEL=sv_rsel)

    sv_sigs, sv_insts, sv_terms = [], [], []
    for i in range(N):
        sv_sigs.append("  wire [%d:0] rd%d;" % (W - 1, i))
        sv_insts.append(
            "  regf_bank #(.ID(%d)) u%d (.clk(clk), .rst(rst), .we(wev),\n"
            "    .wa(wav ^ %d'h%X), .wd(d), .ra(rav ^ %d'h%X), .rd(rd%d));"
            % (i, i, A, i % DEPTH, A, (i * 3 + 1) % DEPTH, i))
        r = i % 32
        if r == 0:
            sv_terms.append("rd%d" % i)
        else:
            sv_terms.append("{rd%d[%d:0], rd%d[%d:%d]}" % (i, W - 1 - r, i, W - 1, W - r))
    sv_top = T("""
module regf_top (input logic clk, input logic rst,
                 input logic [31:0] d, output logic [31:0] q);
  wire        wev = d[31] ^ d[0];
  wire [@AM@:0] wav = d[@AM@:0];
  wire [@AM@:0] rav = d[@AH@:@A@];
@SIGS@
  wire [31:0] acc = @TERMS@;
  always @(posedge clk) begin
    if (rst) q <= 32'h0;
    else     q <= acc;
  end
endmodule
""", AM=A - 1, A=A, AH=2 * A - 1, SIGS="\n".join(sv_sigs),
     TERMS=" ^\n                    ".join(sv_terms))
    sv_top = sv_top.replace("@INSTS@", "")
    sv_top = sv_top.replace("  wire [31:0] acc", "\n".join(sv_insts) + "\n  wire [31:0] acc")
    sv_tb = emit_tb(d, S, CYC)
    wr(d, "regf.sv", sv_bank + sv_top + sv_tb)
    return dict(top="regf_top", tb="regf_tb", sv="regf.sv", cyc=CYC,
                desc="REGISTER-heavy: %d banks x %d x %db = %d flops"
                     % (N, DEPTH, W, N * DEPTH * W))

# ================================================================== MANY
def gen_many(d, p):
    K, M, CYC = p.get("K", 12), p.get("M", 8), p.get("CYC", 2000)
    assert K >= 10 and M >= 8
    S = "many"
    wr(d, "many_stage.vhd", T(FSM_STAGE_V, NAME="many_stage"))

    vs, vi, ss, si = fsm_chain("many_stage", M, idexpr=lambda i: "BASE*%d + %d" % (M, i))
    vq, sq = fsm_collect(M)

    wr(d, "many_cluster.vhd", VHDR + T("""entity many_cluster is
  generic (BASE : natural := 0);
  port (clk, rst : in  std_logic;
        d        : in  std_logic_vector(31 downto 0);
        y        : out std_logic_vector(31 downto 0));
end entity;

architecture rtl of many_cluster is
@SIGS@
  signal acc : std_logic_vector(31 downto 0);
begin
  iv0 <= d(3 downto 0) xor d(7 downto 4) xor d(31 downto 28);

@INSTS@

  acc <= @Q@;

  oreg : process (clk) is
  begin
    if rising_edge(clk) then
      if rst = \'1\' then y <= (others => \'0\');
      else              y <= acc;
      end if;
    end if;
  end process;
end architecture;
""", SIGS=vs, INSTS=vi, Q=vq))

    csig = "\n".join("  signal y%d : std_logic_vector(31 downto 0);" % j for j in range(K))
    csig += "\n" + "\n".join("  signal dx%d : std_logic_vector(31 downto 0);" % j
                              for j in range(K))
    cins = "\n".join(
        "  dx%d <= d xor %s;\n"
        "  c%d : entity work.many_cluster generic map (BASE => %d)\n"
        "    port map (clk => clk, rst => rst, d => dx%d, y => y%d);"
        % (j, hexlit((j * 0x9E3779B9) & 0xFFFFFFFF, 32), j, j, j, j) for j in range(K))
    cxor = " xor ".join("y%d" % j for j in range(K))
    wr(d, "many_top.vhd", VHDR + T("""entity many_top is
  port (clk, rst : in  std_logic;
        d        : in  std_logic_vector(31 downto 0);
        q        : out std_logic_vector(31 downto 0));
end entity;

architecture rtl of many_top is
@CSIG@
begin
@CINS@

  -- DELIBERATE: this assert makes many_top itself untranslatable, so the
  -- --accel scan declines the top and DESCENDS -- installing each cluster as
  -- its own chunk.  That fragmentation is the point of this design
  -- (boundary/handoff stress).  Functionally a no-op; the SystemVerilog
  -- twin carries the equivalent immediate assertion.
  nop : process (clk) is
  begin
    if rising_edge(clk) then
      assert rst = \'0\' or rst = \'1\' report "unreachable" severity note;
    end if;
  end process;

  oreg : process (clk) is
  begin
    if rising_edge(clk) then
      if rst = \'1\' then q <= (others => \'0\');
      else              q <= @CXOR@;
      end if;
    end if;
  end process;
end architecture;
""", CSIG=csig, CINS=cins, CXOR=cxor))

    sv_cluster = T("""
module many_cluster #(parameter int BASE = 0)
  (input logic clk, input logic rst,
   input logic [31:0] d, output logic [31:0] y);
@SIGS@
  assign iv0 = d[3:0] ^ d[7:4] ^ d[31:28];
@INSTS@
  wire [31:0] acc = @Q@;
  always @(posedge clk) begin
    if (rst) y <= 32\'h0;
    else     y <= acc;
  end
endmodule
""", SIGS=ss, INSTS=si, Q=sq)
    sv_csig = "\n".join("  wire [31:0] y%d;\n  wire [31:0] dx%d = d ^ 32'h%08X;"
                        % (j, j, (j * 0x9E3779B9) & 0xFFFFFFFF) for j in range(K))
    sv_cins = "\n".join(
        "  many_cluster #(.BASE(%d)) c%d (.clk(clk), .rst(rst), .d(dx%d), .y(y%d));"
        % (j, j, j, j) for j in range(K))
    sv_top = T("""
module many_top (input logic clk, input logic rst,
                 input logic [31:0] d, output logic [31:0] q);
@CSIG@
@CINS@
  // twin of the VHDL `assert rst = '0' or rst = '1'` in many_top: an SV
  // immediate assertion, ignored by Verilator unless --assert is given, and a
  // no-op either way.  In VHDL it is what makes many_top untranslatable and so
  // fragments the design into one chunk per cluster.
  always @(posedge clk) begin
    assert (rst === 1'b0 || rst === 1'b1);
  end
  always @(posedge clk) begin
    if (rst) q <= 32\'h0;
    else     q <= @CXOR@;
  end
endmodule
""", CSIG=sv_csig, CINS=sv_cins, CXOR=" ^ ".join("y%d" % j for j in range(K)))
    sv_tb = emit_tb(d, S, CYC)
    wr(d, "many.sv", T(FSM_STAGE_S, NAME="many_stage") + sv_cluster + sv_top + sv_tb)
    return dict(top="many_top", tb="many_tb", sv="many.sv", cyc=CYC,
                desc="MANY chunks: %d clusters x %d-stage FSM chain (top declines -> %d chunks)"
                     % (K, M, K))

# ================================================================== ACT
def gen_act(d, p):
    N, W, CYC = p.get("N", 8), p.get("W", 256), p.get("CYC", 2000)
    assert N >= 8 and W % 32 == 0
    CW = max(1, int(math.ceil(math.log2(N))))
    S = "act"
    L = W // 32
    wr(d, "act_stage.vhd", VHDR + """entity act_stage is
  generic (ID : natural := 0; W : positive := 256);
  port (clk, rst, en : in  std_logic;
        d            : in  std_logic_vector(W-1 downto 0);
        q            : out std_logic_vector(W-1 downto 0));
end entity;

architecture rtl of act_stage is
  constant R  : natural := 1 + ((ID*13 + 7) mod (W-1));
  constant K  : natural := (ID*40503 + 12345) mod 65536;
  signal t1, t2, t3, nxt : std_logic_vector(W-1 downto 0);
  signal gt   : std_logic;
begin
  t1  <= std_logic_vector(unsigned(d) + to_unsigned(K, W));
  t2  <= d(W-1-R downto 0) & d(W-1 downto W-R);
  t3  <= t1 xor t2;
  gt  <= '1' when unsigned(t3) > unsigned(d) else '0';
  nxt <= t3 when gt = '1' else std_logic_vector(unsigned(t3) - to_unsigned(K, W));

  reg : process (clk) is
  begin
    if rising_edge(clk) then
      if rst = '1' then      q <= (others => '0');
      elsif en = '1' then    q <= nxt;
      end if;
    end if;
  end process;
end architecture;
"""
    )
    sigs = "\n".join("  signal s%d : std_logic_vector(%d downto 0);" % (i, W - 1)
                     for i in range(N + 1))
    ens = "\n".join("  signal e%d : std_logic;" % i for i in range(N))
    endrv = "\n".join(
        "  e%d <= '1' when (ph = %s) or (mode = '1') else '0';" % (i, hexlit(i, CW))
        for i in range(N))
    insts = "\n".join(
        "  u%d : entity work.act_stage generic map (ID => %d, W => %d)\n"
        "    port map (clk => clk, rst => rst, en => e%d, d => s%d, q => s%d);"
        % (i, i, W, i, i, i + 1) for i in range(N))
    exp = " &\n        ".join("(d xor %s)" % hexlit((i * 0x9E3779B9) & 0xFFFFFFFF, 32)
                              for i in range(L))
    fold = " xor\n        ".join("s%d(%d downto %d)" % (N, 32 * (i + 1) - 1, 32 * i)
                                 for i in range(L))
    wr(d, "act_top.vhd", VHDR + T("""entity act_top is
  port (clk, rst, mode : in  std_logic;
        d              : in  std_logic_vector(31 downto 0);
        q              : out std_logic_vector(31 downto 0));
end entity;

architecture rtl of act_top is
@SIGS@
@ENS@
  signal ph : std_logic_vector(@CM@ downto 0);
  signal fo : std_logic_vector(31 downto 0);
begin
  s0 <= @EXP@;

  pctr : process (clk) is
  begin
    if rising_edge(clk) then
      if rst = '1' then ph <= (others => '0');
      else              ph <= std_logic_vector(unsigned(ph) + 1);
      end if;
    end if;
  end process;

@ENDRV@

@INSTS@

  fo <= @FOLD@;

  oreg : process (clk) is
  begin
    if rising_edge(clk) then
      if rst = '1' then q <= (others => '0');
      else              q <= fo;
      end if;
    end if;
  end process;
end architecture;
""", SIGS=sigs, ENS=ens, CM=CW - 1, EXP=exp, ENDRV=endrv, INSTS=insts, FOLD=fold))

    sv_stage = """
module act_stage #(parameter int ID = 0, parameter int W = 256)
  (input logic clk, input logic rst, input logic en,
   input logic [W-1:0] d, output logic [W-1:0] q);
  localparam int           R = 1 + ((ID*13 + 7) % (W-1));
  localparam logic [W-1:0] K = (ID*40503 + 12345) % 65536;
  wire [W-1:0] t1  = d + K;
  wire [W-1:0] t2  = {d[W-1-R:0], d[W-1:W-R]};
  wire [W-1:0] t3  = t1 ^ t2;
  wire         gt  = (t3 > d);
  wire [W-1:0] nxt = gt ? t3 : (t3 - K);
  always @(posedge clk) begin
    if (rst)     q <= '0;
    else if (en) q <= nxt;
  end
endmodule
"""
    sv_sigs = "\n".join("  wire [%d:0] s%d;" % (W - 1, i) for i in range(N + 1))
    sv_ens = "\n".join("  wire e%d = (ph == %d'h%X) | mode;" % (i, CW, i) for i in range(N))
    sv_insts = "\n".join(
        "  act_stage #(.ID(%d), .W(%d)) u%d (.clk(clk), .rst(rst), .en(e%d), .d(s%d), .q(s%d));"
        % (i, W, i, i, i, i + 1) for i in range(N))
    sv_exp = ",\n                 ".join("(d ^ 32'h%08X)" % ((i * 0x9E3779B9) & 0xFFFFFFFF)
                                         for i in range(L))
    sv_fold = " ^\n                  ".join("s%d[%d:%d]" % (N, 32 * (i + 1) - 1, 32 * i)
                                            for i in range(L))
    sv_top = T("""
module act_top (input logic clk, input logic rst, input logic mode,
                input logic [31:0] d, output logic [31:0] q);
@SIGS@
  logic [@CM@:0] ph;
@ENS@
  assign s0 = {@EXP@};
  always @(posedge clk) begin
    if (rst) ph <= '0;
    else     ph <= ph + 1'b1;
  end
@INSTS@
  wire [31:0] fo = @FOLD@;
  always @(posedge clk) begin
    if (rst) q <= 32'h0;
    else     q <= fo;
  end
endmodule
""", SIGS=sv_sigs, CM=CW - 1, ENS=sv_ens, EXP=sv_exp, INSTS=sv_insts, FOLD=sv_fold)

    extra = dict(
        vg="; ALLON : natural := 0",
        vsig="  signal mode : std_logic;\n",
        vmap=", mode => mode",
        vdrv="  mode <= '1' when ALLON = 1 else '0';\n",
        sp=", parameter int ALLON = 0",
        ssig="  wire mode = (ALLON != 0);\n",
        smap=", .mode(mode)")
    sv_tb = emit_tb(d, S, CYC, extra)
    wr(d, "act.sv", sv_stage + sv_top + sv_tb)
    return dict(top="act_top", tb="act_tb", sv="act.sv", cyc=CYC, gen_extra="ALLON",
                desc="LOW-activity: %d enable-gated stages x %db, 1-of-%d hot "
                     "(ALLON=1 -> all hot, same netlist)" % (N, W, N))

# ----------------------------------------------------------------- main

# ================================================================== ARST
def gen_arst(d, p):
    # Async-reset chain whose TB RE-PULSES rst mid-run.  Regression for the
    # accel drive-path defect chain fixed 2026-08-29 (b06 cc_mux): an async
    # reset assert BETWEEN clock edges must wake the chunk, publish the
    # cleared outputs, and survive the same-timestep NBA edge commit.  The
    # per-cycle fold makes a single stale output cycle change Y.
    N, CYC = p.get("N", 8), p.get("CYC", 20000)
    S = "arst"

    wr(d, "arst_stage.vhd", VHDR + T("""entity arst_stage is
  generic (ID : natural := 0);
  port (clk, rst : in  std_logic;
        d        : in  std_logic_vector(31 downto 0);
        q        : out std_logic_vector(31 downto 0));
end entity;

architecture rtl of arst_stage is
  constant R  : natural := 1 + ((ID*13 + 7) mod 31);
  constant K  : natural := (ID*40503 + 12345) mod 65536;
  signal nxt : std_logic_vector(31 downto 0);
begin
  nxt <= std_logic_vector(unsigned(d(31-R downto 0) & d(31 downto 32-R))
                          + to_unsigned(K, 32)) xor d;

  reg : process (clk, rst) is
  begin
    if rst = '1' then
      q <= std_logic_vector(to_unsigned((ID*257) mod 65536, 32));
    elsif rising_edge(clk) then
      q <= nxt;
    end if;
  end process;
end architecture;
"""))

    sigs = "\n".join("  signal s%d : std_logic_vector(31 downto 0);" % i
                     for i in range(N + 1))
    insts = "\n".join(
        "  u%d : entity work.arst_stage generic map (ID => %d)\n"
        "    port map (clk => clk, rst => rst, d => s%d, q => s%d);" % (i, i, i, i + 1)
        for i in range(N))
    wr(d, "arst_top.vhd", VHDR + T("""entity arst_top is
  port (clk, rst : in  std_logic;
        d        : in  std_logic_vector(31 downto 0);
        q        : out std_logic_vector(31 downto 0));
end entity;

architecture rtl of arst_top is
@SIGS@
begin
  s0 <= d;
@INSTS@
  q <= s@N@;
end architecture;
""", SIGS=sigs, INSTS=insts, N=N))

    repulse = ("  rst <= '1' when (cnt < 4) or"
               " (cnt >= 500 and (cnt mod 509) < 2) else '0';")
    sv_repulse = ("  wire         rst  = (cnt < 32'd4) ||"
                  " ((cnt >= 32'd500) && ((cnt % 32'd509) < 32'd2));")
    svtb = emit_tb(d, S, CYC, extra=dict(rstdrv=repulse, svrst=sv_repulse))

    svstages = "\n".join(T("""module arst_stage_@I@ (
  input  wire        clk, rst,
  input  wire [31:0] d,
  output reg  [31:0] q);
  localparam [31:0] K = (@I@*32'd40503 + 32'd12345) % 32'd65536;
  localparam integer R = 1 + ((@I@*13 + 7) % 31);
  wire [31:0] nxt = ({d[31-R:0], d[31:32-R]} + K) ^ d;
  always @(posedge clk or posedge rst)
    if (rst) q <= (@I@*32'd257) % 32'd65536;
    else     q <= nxt;
endmodule
""", I=i) for i in range(N))
    svinsts = "\n".join(
        "  arst_stage_%d u%d (.clk(clk), .rst(rst), .d(s%d), .q(s%d));" % (i, i, i, i + 1)
        for i in range(N))
    svsigs = "\n".join("  wire [31:0] s%d;" % i for i in range(N + 1))
    wr(d, "arst.sv", T("""@STAGES@
module arst_top (
  input  wire        clk, rst,
  input  wire [31:0] d,
  output wire [31:0] q);
@SIGS@
  assign s0 = d;
@INSTS@
  assign q = s@N@;
endmodule

@TB@
""", STAGES=svstages, SIGS=svsigs, INSTS=svinsts, N=N, TB=svtb))
    return dict(top="arst_top", tb="arst_tb", sv="arst.sv", cyc=CYC,
                desc="async-reset chain N=%d, TB re-pulses rst (b06 class)" % N)

SHAPES = dict(wide=gen_wide, fsm=gen_fsm, deep=gen_deep,
              regf=gen_regf, many=gen_many, act=gen_act,
              arst=gen_arst)

if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in SHAPES:
        sys.exit("usage: gen.py <%s> <outdir> [K=V ...]" % "|".join(SHAPES))
    shape, outdir = sys.argv[1], sys.argv[2]
    params = {}
    for a in sys.argv[3:]:
        k, v = a.split("=", 1)
        params[k] = int(v)
    os.makedirs(outdir, exist_ok=True)
    info = SHAPES[shape](outdir, params)
    # dependency order for `nvc -a`: leaves, cluster, top, tb
    rank = {"_stage": 0, "_bank": 0, "_cluster": 1, "_top": 2, "_tb": 3}
    files = sorted([f for f in os.listdir(outdir) if f.endswith(".vhd")],
                   key=lambda f: min((v for k, v in rank.items() if k in f), default=1))
    info["order"] = " ".join(files)
    with open(os.path.join(outdir, "INFO"), "w") as f:
        for k, v in info.items():
            f.write("%s=%s\n" % (k, v))
    print("%s -> %s : %s" % (shape, outdir, info["desc"]))
