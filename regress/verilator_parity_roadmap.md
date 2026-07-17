# Verilator-parity roadmap: the 1112 translation gaps, categorized and ranked

Produced 2026-07-16 by a 21-agent diagnosis of every SHIM_NO_OUTPUT / SHIM_ERROR
test in the value-reference sweep (1111/1112 individually diagnosed with evidence;
per-test data in out/vl_parity_pertests.json). Unlock counts are approximate upper
bounds -- see Audit at the end. Context: 826 of the 859 no-output tests already
compile end-to-end; their $display output is dropped in translation, so most of
this is feature emission work, not architecture.

Goal (north star, raised 2026-07-16): be BETTER than Verilator in every dimension
we can; language coverage is the dimension where we are behind.

## 1. REAL type end-to-end (VHDL_TYPE_REAL)
**Unlocks ~185 tests · large · tgt-vhdl vhdl_type/expr/stmt/cast/lpm + sv2vhdl VHDL lib**

vhdl_type.hh has no REAL at all (only std_logic/logic3d/signed/unsigned/integer...), so ~185 tests die on any real-typed signal, parameter, port, cast, or $realtime. Add VHDL_TYPE_REAL mapped to VHDL's native `real`, wire IVL_VT_REAL through scope.cc declarations, expr.cc operators, cast.cc conversions (integer<->real, vector<->real via a sv2vhdl to_real/to_vec pair), lpm.cc, and display formatting (%f/%g/%e). Map $rtoi/$itor/$realtobits/$bitstoreal/$realtime and math.* / VAMS abs/min/max/pow to VHDL ieee.math_real functions in the lib. Two-state byte/int/shortint casts fall out of the same cast.cc coercion table. This is the single largest unlock; do it first and in layers (scalar var arithmetic first ~116, then nets/arrays/ports/params).

*Categories:* real-arithmetic, real-array, real-valued-net, wire-real, real-net-continuous-assign, real-case, case-real, real-cast, cast-lpm-unsupported, realtime-unsupported, system-function-realtime, sysfunc-realtime, system-function-realtobits, sysfunc-realtobits, realtobits-unsupported, rtoi-system-function, sysfunc-rtoi-itor, real-variable-delay, real-parameter, real-net, real-module-port, real-specparam, real-wire-array, real-net-array, real-gate-input, real-to-int-cast-lpm, real-valued-function, real-math-functions, math-system-function, scaled-real-literal, abstime-unsupported, sysfunc-abstime, real-to-struct-cast, shortreal-system-function, verilog-ams-abs, vams-abs-builtin, vams-abs-function, ams-math-function, ams-system-functions, ams-abs-builtin, vams-flag-dropped, power-operator-in-ca, two-state-cast-lpm, two-state-cast, two-state-cast-ca, two-state-int-cast

## 2. force/release + procedural assign/deassign via nvc VHDL-2008 force/release
**Unlocks ~67 tests · medium · tgt-vhdl stmt.cc + nvc**

stmt.cc:2535 hard-errors on force/release. nvc already implements VHDL-2008 `force`/`release` signal assignment, which is a near-direct semantic match: emit `sig <= force val;` / `sig <= release;` for Verilog force/release, and the same mechanism (with a shadow driver process) for procedural assign/deassign and PCA. Wires with existing drivers and forced part-selects need a shadow full-signal force (read-modify-force); real targets ride on item 1. Verify nvc's force wins over logic3d resolved nets, patch nvc if not.

*Categories:* force-release, procedural-continuous-assign, procedural-assign-deassign, assign-deassign-real

## 3. String literals in vector/expression contexts
**Unlocks ~62 tests · medium · tgt-vhdl expr.cc + stmt.cc draw_stask_display**

Verilog string literals are just packed vectors; the backend currently only handles them in pure $display arg position, so ternaries (tern3), assignments, comparisons, and tasks-as-functions fail. In expr.cc, when a string literal appears in a vector context, emit it as a logic3d_vector constant (8 bits/char); conversely teach draw_stask_display to render vector-valued %s and to pass through tabs/escapes/stray-% correctly, and add %b/%h/%o whole-value radix variants ($displayb/$displayh). Mostly localized literal-lowering plus display-formatter hardening.

*Categories:* string-literal-context, tab-in-string-literal, string-literal-escape, display-format-stray-percent, display-arg-count-mismatch, display-radix-variant, displayb-only-output, displayh-only-output, displayb-unsupported, displayh-unsupported, system-task-displayb

## 4. Package and $unit (compilation-unit) scope lowering
**Unlocks ~55 tests · medium · sv-normalize + bin/sv-pkg-to-vhdl + tgt-vhdl scope.cc**

A long tail of ~22 categories is the same root cause: identifiers living in SV packages or $unit scope never get a VHDL home. Extend the existing sv-pkg-to-vhdl/sv-flatten-pkg path: sv-normalize hoists $unit declarations into a synthetic package, packages emit as VHDL packages (constants, types, functions, tasks-as-procedures), and package variables (mutable state) go into the scope-keyed store in state.cc or a shared-variable-in-package emission. scope.cc then resolves pkg::x to pkg_name.x. Block-scope vars fold in by hoisting to process scope with unique names.

*Categories:* unit-scope-variable, compilation-unit-scope-var, package-scoped-function, package-scope-var, package-scoped-identifier, package-variable-ref, package-function, package-scope-signal, package-scoped-var, compilation-unit-var, compilation-unit-scope-decl, package-scoped-variable, package-scope-task, unit-scope-declaration, package-scoped-task, unit-scope-task, package-import, package-scope-variable, package-func-backend-assert, block-scope-var, block-scope-var-initializer, named-block-local-var

## 5. Signedness / scalar-vector / boolean coercion batch
**Unlocks ~70 tests · medium · tgt-vhdl expr.cc + cast.cc**

VHDL's strong typing makes every operand-type mismatch a compile failure where Verilog silently coerces. Build one central coercion helper: coerce_operands(op, lhs, rhs, ctx_width, ctx_signed) that inserts resize/to_signed/to_unsigned/logic3d casts per Verilog's self-determined/context-determined width rules, and a to_value() that maps boolean comparison results into '0'/'1' vectors in value contexts. Route all binary/unary/ternary emission through it. Add the few missing operators (==?, ->, <->) as sv2vhdl lib functions. ~29 diagnosed categories collapse into roughly a dozen call sites; batch-verify against the pr3054101* family.

*Categories:* scalar-vector-op-mismatch, scalar-vector-operand-mix, unary-minus-signed-result-unconverted, unary-minus-signed-result-context, ternary-signed-vector-mix-op, ternary-signed-operand-conversion, int-literal-vector-mix-op, bool-comparison-result-context, comparison-as-value-context, bool-compare-in-value-context, bool-in-arith-context, input-port-coercion, shift-amount-not-integer, shift-on-scalar, rising-edge-on-vector, scalar-vector-width-mix-op, resize-on-array-type, casex-label-type-mismatch, case-expr-width-truncation, duplicate-case-choice, signed-to-stdlogic-cast, power-op-to-unsigned-context, to-signed-overload, concat-overload-ambiguity, wildcard-equality-op, logical-implication-op, logical-equivalence-op, equiv-operator, slice-of-scalar

## 6. $monitor / $strobe / $fmonitor engine
**Unlocks ~54 tests · medium · tgt-vhdl stmt.cc + sv2vhdl VHDL lib + state.cc**

$monitor is entirely unimplemented (48 tests produce no output at all). Reuse the $display formatter: on $monitor, register the format+arg signal list with a singleton monitor process emitted per design that is sensitive to all monitored signals, prints at most once per delta-settled time step (postponed-process semantics; the scope-keyed store in state.cc already handles $time/%m plumbing). $strobe is the one-shot version of the same end-of-timestep print; $monitoron/off is a flag in the store. The verilator_ref work already proved the display formatter is solid, so this is scheduling machinery, not formatting.

*Categories:* monitor-only-output, strobe-only-output, monitor-strobe-unsupported, file-io-tasks, no-display-in-source

## 7. Array/memory indexing and part-select correctness batch
**Unlocks ~50 tests · medium · tgt-vhdl expr.cc + stmt.cc lvalue emission**

A correctness cluster, not a feature: word indexes silently dropped when a bit/part-select follows (select6/7/8), indexes not rebased for non-zero/negative bounds, and no OOB clamping (Verilog reads x, writes are ignored; VHDL traps). Fix lvalue/rvalue emission to compose arr(word_idx)(msb downto lsb) instead of losing the word index, normalize indexes with a single rebase helper (idx - lsb_bound with direction), and wrap runtime-variable indexes in sv2vhdl lib functions safe_read/safe_write that return x / no-op when out of bounds. High test-per-line-of-code ratio.

*Categories:* part-select-oob, oob-part-select-array-word, oob-part-select-vector, mem-word-index-dropped, array-word-bit-select-dropped, array-index-base-mismatch, mem-index-base-normalization, memory-word-part-select, array-word-assign-index-dropped, array-word-part-select-dropped-index, nonzero-array-index-rebase, negative-bound-index-remap, array-index-oob, array-word-select-as-part-select, casex-scalar-bit-index, array-word-bit-select, per-signal-array-type, real-array, multidim-array, multidim-unpacked-array

## 8. SV procedural statement lowering: do-while, break/continue, ++/--, degenerate for-loops
**Unlocks ~32 tests · small · tgt-vhdl stmt.cc (with sv-normalize fallback for ++/--)**

VHDL natively has everything needed: do-while becomes `loop body; exit when not cond; end loop`, break/continue map 1:1 to exit/next, empty for-init/cond/step are just a while-true loop with optional guard, and empty if-branches emit `null;`. i++/i-- lower to i := i + 1 in sv-normalize or directly in stmt.cc. disable-of-enclosing-block maps to exit from a named loop. These are each afternoon-sized stmt.cc cases sharing test infrastructure.

*Categories:* do-while-loop, do-while-statement, break-continue, break-continue-stmt, break-statement, increment-operator, increment-decrement-operator, for-loop-empty-step, for-loop-empty-init, empty-for-condition, empty-for-init, infinite-for-break, empty-if-branch, disable-statement-noop

## 9. Top-level detection: multiple tops, program blocks, deferred stubs
**Unlocks ~26 tests · small · tgt-vhdl scope.cc + iverilog-sv2ghdl driver**

The driver/scope walker picks exactly one top and mis-handles `program` blocks and stub-deferred elaboration, so whole testbenches emit empty. Emit every root scope as an entity and generate a synthetic wrapper top that instantiates all of them (or pass multiple tops to nvc -e via the driver script). Treat program blocks as modules with end-of-timestep scheduling ignored (fine for these tests). Fix the deferred-stub path that currently yields empty architectures when elaboration order defers a module.

*Categories:* multiple-top-modules, program-block-top-detection, top-module-detection, program-block, deferred-stub-elab-empty, unconnected-port, v95-implicit-port

## 10. Class-scope backend assert + function/task default arguments
**Unlocks ~26 tests · small · tgt-vhdl scope.cc + stmt.cc task/function emission**

The 23 class-scope-backend-assert tests (sv_port_default*) are default-argument tests that crash the backend when iverilog represents defaults via class-like scopes — the fix is to stop asserting: skip IVL_SCT_CLASS scopes gracefully, and bind default argument expressions at call sites (VHDL supports parameter defaults directly on procedure/function declarations, so emission is natural). Distinct from real class support — this is crash-proofing plus a feature VHDL already has.

*Categories:* class-scope-backend-assert, sv-task-port-types, task-port-sv-types, task-port-redeclaration

## 11. Identifier mangling and entity-dedup naming consistency
**Unlocks ~24 tests · small · tgt-vhdl scope.cc/support.cc + bin/sv-dedup-vhdl**

Two related naming bugs: (a) escaped/reserved-word/dotted Verilog identifiers collide or emit illegal VHDL — centralize one mangle_name() (escape reserved words, replace illegal chars, uniquify against a per-scope symbol table, covering UDP temp names too); (b) the entity dedup/parameter-hash suffix logic disagrees with itself between the entity name and attribute/instantiation references — make dedup derive both from the same canonical key so pr2922063*/zero_repl stop mismatching. Small, mechanical, and eliminates a class of flaky name bugs that also bites big designs.

*Categories:* reserved-word-identifier-escape, identifier-mangling, dot-identifier-escape, escaped-identifier-emission, entity-suffix-attr-mismatch, dedup-entity-attr-name-mismatch, entity-hash-attribute-mismatch, attribute-entity-name-mismatch, string-param-dedup-name, entity-dedup-assert, udp-input-tmp-collision

## 12. Named events, event triggers, and intra-assignment event controls
**Unlocks ~32 tests · medium · tgt-vhdl stmt/process + sv-normalize**

Model each named event as a std_ulogic toggle signal: `->e` emits e <= not e (or a lib trigger() using the 'transaction attribute), `@e` waits on e'transaction — nvc supports 'transaction. Intra-assignment event controls (v = @(posedge clk) expr, and the nb_ec_* family) lower in sv-normalize to: capture RHS into a temp, wait for the event, then assign — the diagnosis shows they are currently silently dropped, which is a wrong-answer bug, not just a gap. Processes mixing wait-statements with sensitivity lists must emit as sensitivity-free processes with explicit waits.

*Categories:* named-event-trigger, event-trigger, hierarchical-event-trigger, intra-assignment-event-control-dropped, empty-sensitivity-list, wait-in-sensitivity-process, event-attr-in-sensitivity-list, sv-normalize-setval-intra-assign-delay, sv-normalize-setval-rewrite

## 13. fork/join, join_any, join_none, disable fork
**Unlocks ~45 tests · large · sv-normalize + tgt-vhdl stmt.cc + sv2vhdl VHDL lib**

stmt.cc:2552 hard-errors. VHDL has no dynamic processes, but ivtest forks are statically known: hoist each fork branch into its own generated process gated by a start toggle signal, with a done-counter for join (wait until done_cnt = N), first-done event for join_any, and no wait for join_none; `disable fork` sets an abort flag branches poll at wait points. sv-normalize does the hoisting (branch bodies become processes parameterized by parent locals passed through shared temps); the backend only needs the handshake emission. Large but self-contained, and the 45 tests include core scheduling tests (clkgen patterns) that gate other suites.

*Categories:* fork-join, fork-join-none, fork-join-any, fork-join-untranslatable, display-in-fork-join

## 14. Dynamic arrays and queues on the scope-keyed store
**Unlocks ~60 tests · large · tgt-vhdl state.cc/expr/stmt + sv2vhdl VHDL lib (VHPI C side)**

Follow the pattern that already works for the scope-keyed store: represent each dynamic array/queue as an integer handle into a C-side store (state.cc grows darray_new/resize/size/get/put/push_back/push_front/pop, element payload = logic3d words, real, or string), with VHDL wrapper functions in the lib. The queue-state-*-undeclared categories show partial plumbing already exists but declarations aren't emitted — finish declaration emission first (cheap wins), then the method set. foreach over dynamic arrays lowers to an index loop over size().

*Categories:* dynamic-array, dynamic-array-new, darray-expr-untranslated, real-darray-expr-untranslated, real-expression-unsupported, real-expression-untranslated, queue, queue-method-push-front, queue-state-signals-undeclared, queue-state-undeclared, queue-state-vars-undeclared, q-system-tasks, string-darray-element-emission, darray-assign-pattern-untranslated

## 15. System functions in continuous assigns + bit-count/reduction sysfuncs
**Unlocks ~33 tests · medium · tgt-vhdl lpm.cc + sv2vhdl VHDL lib**

lpm.cc bails when a continuous assign contains a system function or ** operator. Two-part fix: (a) implement $countones/$onehot/$onehot0/$isunknown/$countbits/$clog2-of-signal as pure functions in the sv2vhdl lib (trivial loops over logic3d_vector); (b) for CA expressions lpm.cc can't map, fall back to emitting a concurrent process (the general expr.cc path) instead of erroring — that fallback also catches future unsupported-in-CA cases. ** emits a lib pow_uv/pow_sv function (repeated-squaring over vectors) covering all power-operator categories.

*Categories:* sfunc-in-continuous-assign, system-function-in-continuous-assign, system-function-unsupported, system-function-onehot, system-function-onehot0, system-function-isunknown, system-function-countbits, delayed_sfunc, pow-operator-lpm, power-operator, power-op-continuous-assign, power-operator-ca, implicit-cast-lpm

## 16. Hierarchical cross-module references via VHDL-2008 external names
**Unlocks ~23 tests · medium · tgt-vhdl expr.cc/scope.cc + nvc**

Map Verilog hierarchical signal refs (top.u1.sig) to VHDL-2008 external names (<< signal ^.u1.sig : logic3d_vector >>), which nvc supports; the backend needs to compute the path relative to the referencing scope and know the target's VHDL type (already available from the ivl scope tree). Hierarchical task calls: since tasks emit as procedures in the target scope's package or process, redirect the call through a shared-variable/store-based shim. defparam is resolved by iverilog at elaboration already — likely just a scope-walk fix.

*Categories:* hierarchical-ref, hierarchical-name-reference, hierarchical-task-call, defparam

## 17. File I/O and scanf family ($fscanf/$sscanf/$fread/$fdisplay/$swrite)
**Unlocks ~30 tests · medium · state.cc (VHPI C side) + tgt-vhdl stmt.cc + sv2vhdl VHDL lib**

VHDL textio can't express %-format scanning, so put the real work in C behind the existing VHPI boundary: state.cc gains fopen/fscanf/sscanf/fread/fdisplay entry points that implement Verilog format semantics (including %z, 4-state read into logic3d) and return values/consumed-count to VHDL wrappers. $fdisplay/$swrite reuse the display formatter with an fd argument. $value$plusargs reads the plusarg list the driver already passes to nvc. Deliberately after monitor/display work so the formatter is shared, not duplicated.

*Categories:* file-io-unsupported, sscanf-unsupported, system-function-sscanf, system-function-fscanf, fscanf-unsupported, sysfunc-fscanf, sscanf-system-function, file-io-functions, file-io, system-function-fread, system-task-fdisplay, fdisplay-only-output, swrite-unsupported, writemem-readmem-system-task, value-plusargs, simparam

## 18. SV string variable type and methods
**Unlocks ~20 tests · medium · tgt-vhdl vhdl_type/expr + state.cc + sv2vhdl VHDL lib**

Dynamic-length strings can't be VHDL `string` signals (fixed length). Represent string variables as handles into the C-side store (same infra as item 14): state.cc string ops (len/substr/putc/getc/itoa/compare/concat), lib-side wrapper functions, and $sformatf reusing the display formatter into a store string. String indexing s[i] and iteration fall out of getc. Do after items 6/14 — it reuses both the formatter and the handle-store pattern.

*Categories:* string-method, string-method-unsupported, string-method-substr, string-var-unsupported, string-index, sformatf-unsupported, system-function-sformatf, sysfunc-sformatf

## 19. Port declaration edge cases batch
**Unlocks ~22 tests · medium · tgt-vhdl scope.cc + sv-normalize**

Cluster of entity-generation bugs: unpacked/multidim array ports need their array type declared in a package the entity can see (emit per-design types package instead of per-architecture types); empty port lists should emit no port clause; non-ANSI redeclaration widths must use the resolved ivl signal, not the port decl; port expressions that are concats/part-selects need an adapter signal + continuous assign in the instantiating architecture. Each sub-item is small; grouped because they share the entity-emission code path in scope.cc.

*Categories:* empty-port-list, unpacked-port-type-undeclared, array-port-type-undeclared, port-typedef-scope, nonansi-port-width-redecl, var-input-port, port-expression-concat, port-expr-part-select, module-port-array-initializer, multidim-array-port, g2012-port-redeclaration, input-port-coercion

## 20. Functions with side effects, void functions, impure marking, static initializers
**Unlocks ~55 tests · medium · tgt-vhdl stmt.cc/scope.cc function emission**

One coherent rework of function/task emission: (a) emit `impure function` whenever the body reads a signal (pure-function-signal-ref is likely a one-word fix worth 11 tests alone — do it day one); (b) functions that write module vars or do part-select writes to outer state get lowered to procedures with inout params or store-mediated writes, with call sites rewritten (void functions become procedures directly); (c) static local initializers hoist to a one-shot init at process start, automatic vars just become VHDL variables (naturally per-call in procedures); (d) functions declared in generate blocks emit into the generated block's scope. Ordered mid-list but the impure-marking sub-fix should be cherry-picked immediately.

*Categories:* pure-function-signal-ref, void-function, always-comb-void-func, void-function-return, function-module-var-side-effect, function-side-effect-assign, function-side-effect, module-var-write-in-function, blocking-assign-writeback-deferred, part-select-write-in-function, function-return-part-select, static-function-local-init, static-function-var-init, static-var-init-in-function, static-task-var-init, task-static-var-init, task_init_var, module-lifetime-qualifier, module-automatic-lifetime, automatic-task-alloc, automatic-task, automatic-task-call, function-in-generate-block, function-in-generate, task-function-in-generate

## 21. Small semantic batch: enum methods, assignment patterns, timescale tasks, wait/final/misc
**Unlocks ~40 tests · medium · tgt-vhdl expr/stmt + sv2vhdl VHDL lib**

Aggregation of independent small features: enum .name/.next/.prev/.first/.last emit as generated per-enum lookup functions in the types package; assignment patterns '{...} lower to VHDL aggregates (positional/named/others map 1:1 — the aggregate machinery already exists per the accel work); $printtimescale prints from the timescale info the backend already tracks; final blocks emit as a process waiting on a simulation-end signal from the store (nvc endsim hook) instead of running at t=0; time arithmetic needs 64-bit handling instead of integer. Grouped for scheduling, not because they share code.

*Categories:* enum-method, assignment-pattern, unpacked-array-pattern-cassign, system-task-printtimescale, printtimescale-only-output, printtimescale-module-arg, parameter-value-range, time-arithmetic, time-literal-overflow, final-block, final-block-runs-at-time-zero, type-parameter, udp-table, concat3, array-continuous-assign

## 22. VCD dumping ($dumpvars/$dumpfile)
**Unlocks ~6 tests · medium · nvc + iverilog-sv2ghdl driver**

nvc already has wave dumping (FST/VCD); the cheapest route is driver-level: detect $dumpvars in source and pass nvc's wave-dump flags, plus a store-side shim that creates the named dump file so tests checking file existence/content pass. Only worth doing at fidelity these 6 tests need — proper $dumpvars scope arguments can map to nvc's include-signal globs. Low count, but VCD output also aids debugging everything else.

*Categories:* dumpvars-only-output, dumpvars-only-no-display, vcd-dump-only-output, sdf-annotate-omitted

## 23. SystemVerilog classes
**Unlocks ~87 tests · architectural · sv-normalize (devirtualization) + state.cc object store + tgt-vhdl**

Out of scope for Verilog-2005 parity — explicitly deferred. When tackled: the viable path is handle-based objects in the C-side store (properties as typed slots, methods as procedures taking the handle, `new` allocates), with sv-normalize doing monomorphization/devirtualization for the non-polymorphic subset that covers most ivtest class tests. Requires the dynamic-array/queue/string store infrastructure (items 14/18) as prerequisites, which is another reason those rank where they do.

*Categories:* class-based-test, class-expr-untranslated, class-expression-unsupported, sv-class-expression-untranslated, class-static-property, class-property-access, class-method-call, sv_root_class

## Grounding notes (from the synthesis pass)

Grounding: verified against the actual backend at /usr/local/src/iverilog/tgt-vhdl — vhdl_type.hh has NO real type (confirms item 1 is a true type-system gap, not scattered bugs); stmt.cc:2535 and :2552 are hard errors for force/release and fork (items 2, 13); $monitor/$strobe have no code path at all (item 6); state.cc (426 lines) is the scope-keyed C store to build handle-based dynamic types on (items 14, 17, 18, 23). bin/ already contains sv-pkg-to-vhdl and sv-flatten-pkg, which is why package/$unit scoping (item 4) is medium rather than large.

Accounting: the 23 items sum to ~1100 nominal unlocks against 1112 gaps; per-item numbers are the summed diagnosis-category counts and overlap somewhat (e.g. a real-typed queue test needs items 1+14; sv_queue_* tests appear under automatic-task categories), so treat unlocks as upper bounds per item. Roughly 40 singleton categories (negative-compile-test, udp-table pr707, sdf_header, simparam, l_impl, etc.) are folded into the nearest batch or intentionally unlisted (~12 tests of pure long tail).

Ranking heuristic: unlocks divided by difficulty weight (small=1, medium=2.5, large=6), then adjusted for dependency order — display-formatter reuse pushes monitor (6) before file-io (17) and sformatf (18); the store-handle pattern pushes darray/queue (14) before string vars (18) and classes (23). Items 1-11 are the high-ROI front: completing just those is worth roughly 600+ nominal unlocks and would take the suite from 1038/2606 toward the ~1700 range even after overlap discount.

Quick wins worth cherry-picking out of order: impure-function marking (11 tests, likely a one-word emission change, inside item 20); queue-state declaration emission (the *-undeclared categories in item 14 suggest plumbing exists and only declarations are missing); class-scope assert crash-proofing (item 10) since it is crash-removal, not feature work.

Risk notes: item 2 depends on nvc's VHDL-2008 force/release interacting correctly with logic3d resolved nets — prototype force_release_wire_pv first; if nvc's force loses to resolution, a shadow-driver fallback in the lib is the plan B. Item 12's intra-assignment event controls are currently silently DROPPED (wrong results, not errors) — that subcategory is a correctness bug worth fast-tracking. Item 13 (fork) branch-hoisting in sv-normalize must respect the relay/inlining rules that bit us before (memory: nvc silently drops <= after := on same signal — keep deposit-consistency discipline in generated handshake processes).

## Audit (adversarial completeness critic)

The critic verified: no category with >10 tests is missing from the roadmap, and
the single 'architectural' rating (SV classes) is genuine. It found the following
accounting defects -- treat per-item unlock numbers as upper bounds until fixed:

- Q1 DOUBLE COUNTING: 8 tests are counted in two roadmap items each — real-array (5 tests) appears in both item 1 (REAL end-to-end) and item 7 (array indexing batch); input-port-coercion (3 tests) appears in both item 5 (signedness batch) and item 19 (port edge cases). Claimed total 1104 vs 1102 unique tests actually reachable via the listed categories.
- Q1 PER-ITEM MISMATCHES: 13 of 23 items claim unlocks unequal to the true sum of their listed categories (diffs -15..+4). Worst: item 1 claims 185 but its category list sums to 200 — the gap is exactly the four two-state-cast* categories (15 tests) that are in the list but excluded from the count (list or count, one is wrong); item 7 claims 50 vs true 58; item 18 claims 20 vs true 16 (+4 overclaim, inflated by the nonexistent 'string-index' entry).
- Q1 PHANTOM ENTRIES: 7 roadmap 'categories' are not categories. Five are individual test names (concat3->item 21, defparam->item 16, delayed_sfunc->item 15, simparam->item 17, sv_root_class->item 23) whose real categories are already listed elsewhere, i.e. test-level double listing; two ('string-index' item 18, 'task_init_var' item 20) match no category and no test.
- Q1 DROPPED: 9 categorized tests in 7 categories map to no roadmap item — the $countdrivers family is 5 tests fragmented across 3 synonym slugs (countdrivers-unsupported=3, countdrivers-system-function=1, system-function-countdrivers=1), plus simparam-system-function, negative-compile-test, no-output-statements, system-task-unsupported (1 each). Notes claim '~12 tests of pure long tail' unlisted; actual is 9.
- Q1 COVERAGE GAP: the 1 uncategorized test (1111 of 1112) is always3.1.3F2, present in slice zero_02.json but skipped by its categorizer agent; it should be back-filled.
- Q2 ANSWER: PASS — no category with >10 tests is missing from the roadmap. All 15 categories above 10 (real-arithmetic 116 down to pure-function-signal-ref 11) map to items; the largest unmapped root cause even after merging synonym slugs is $countdrivers at 5 tests.
- Q3 ANSWER: PASS — the only 'architectural' item (23, SV classes, 87) is genuinely architectural: sampled evidence shows IVL_EX_NEW/EX_PROPERTY untranslated and state.cc:187/expr.cc:659 asserts inside class methods, i.e. real class-semantics dependencies; the crash-only class-scope-backend-assert cluster (23 tests) was correctly separated into small item 10. Minor caveats: item 14 (large) embeds 3 queue-state-*-undeclared tests the roadmap's own notes flag as a likely small declaration-emission fix, and item 13's 'large' rating is driven by join_any/join_none while plain static fork..join (36 of its 45) may be a medium sv-normalize rewrite — judgment calls, not mislabels.
- RECOMMENDATION: treat per-item unlock numbers as approximate upper bounds until fixed — correct item 1 to 185 by removing the two-state-cast* categories to item 5 (or restate as 200), remove real-array from item 7 or item 1, remove input-port-coercion from item 19 or item 5, delete the 7 phantom entries, and back-fill always3.1.3F2.
