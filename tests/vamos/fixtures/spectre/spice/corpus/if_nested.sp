* t
.if (a>1)
.if (b>2)
R1 a b 1k
.else
R1 a b 2k
.endif
.else
R2 a b 3k
.endif

