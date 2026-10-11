* expression operator coverage
.param a=2 b=3 c=4
.param p1={a+b-c*a/b}
.param p2={a**b}
.param p3={(a>b) ? a : b}
.param p4={a>=b && b<=c || a==c}
.param p6={sqrt(a)+pow(b,2)}
V1 in 0 DC {a+b}
R1 in out {p1+p2}
C1 out 0 1n
.op
.end
