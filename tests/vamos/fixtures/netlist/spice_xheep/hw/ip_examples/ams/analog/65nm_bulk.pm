* A subset of the PTM 65 nm bulk CMOS BSIM4 model cards, 65nm_bulk.pm ("Beta
* Version released on 2/22/06"), from the Predictive Technology Model
* (Nanoscale Integration and Modeling Group, Arizona State University,
* http://ptm.asu.edu/).  x-heep's adc.sp includes that file, and x-heep does
* not ship it.  Both cards keep PTM's names (nmos, pmos), level 54 and
* 'key = value' layout, and 51 of the 214 parameters of each PTM card (level
* included), at PTM's values except version = 4.5 (PTM: 4.0), igcmod,
* igbmod, rbodymod and rgatemod = 0 (PTM: 1; the parameters those models use
* are not kept) and the pmos rdsw = 315 (PTM: 165).
* PTM asks every user to acknowledge http://ptm.asu.edu/ and its publications:
* W. Zhao, Y. Cao, "New generation of Predictive Technology Model for sub-45nm
* early design exploration," IEEE Transactions on Electron Devices, vol. 53,
* no. 11, pp. 2816-2823, November 2006; Y. Cao, T. Sato, D. Sylvester,
* M. Orshansky, C. Hu, "New paradigm of predictive MOSFET and interconnect
* modeling for early circuit design," CICC, pp. 201-204, 2000.
* (tests/vamos/fixtures/README has the sources of the third-party fixtures.)

.model  nmos  nmos  level = 54

+version = 4.5             binunit = 1               paramchk= 1               mobmod  = 0
+capmod  = 2               igcmod  = 0               igbmod  = 0               geomod  = 1
+diomod  = 1               rdsmod  = 0               rbodymod= 0               rgatemod= 0

+tnom    = 27              toxe    = 1.85e-009       toxp    = 1.2e-009        toxm    = 1.85e-009
+epsrox  = 3.9             wint    = 5e-009          lint    = 5.25e-009       xj      = 1.96e-008
+vth0    = 0.423           k1      = 0.4             k2      = 0.01            dvt0    = 1
+dvt1    = 2               dsub    = 0.1             ndep    = 2.54e+018       ngate   = 2e+020
+voff    = -0.13           nfactor = 1.9             eta0    = 0.0058          u0      = 0.0491
+ua      = 6e-010          ub      = 1.2e-018        vsat    = 124340          a0      = 1.0
+pclm    = 0.04            pdiblc1 = 0.001           pdiblc2 = 0.001           delta   = 0.01
+rdsw    = 165             cgso    = 1.5e-010        cgdo    = 1.5e-010        cgbo    = 2.56e-011
+cjs     = 0.0005          cjd     = 0.0005          mjs     = 0.5             mjd     = 0.5
+kt1     = -0.11           ute     = -1.5

.model  pmos  pmos  level = 54

+version = 4.5             binunit = 1               paramchk= 1               mobmod  = 0
+capmod  = 2               igcmod  = 0               igbmod  = 0               geomod  = 1
+diomod  = 1               rdsmod  = 0               rbodymod= 0               rgatemod= 0

+tnom    = 27              toxe    = 1.95e-009       toxp    = 1.2e-009        toxm    = 1.95e-009
+epsrox  = 3.9             wint    = 5e-009          lint    = 5.25e-009       xj      = 1.96e-008
+vth0    = -0.365          k1      = 0.4             k2      = -0.01           dvt0    = 1
+dvt1    = 2               dsub    = 0.1             ndep    = 1.87e+018       ngate   = 2e+020
+voff    = -0.126          nfactor = 1.9             eta0    = 0.0058          u0      = 0.00574
+ua      = 2.0e-009        ub      = 0.5e-018        vsat    = 70000           a0      = 1.0
+pclm    = 0.12            pdiblc1 = 0.001           pdiblc2 = 0.001           delta   = 0.01
+rdsw    = 315             cgso    = 1.5e-010        cgdo    = 1.5e-010        cgbo    = 2.56e-011
+cjs     = 0.0005          cjd     = 0.0005          mjs     = 0.5             mjd     = 0.5
+kt1     = -0.11           ute     = -1.5
