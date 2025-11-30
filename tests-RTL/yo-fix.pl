#!/usr/bin/env perl

$trans{AND}       = "and";
$trans{OR}        = "or";
$trans{NAND}      = "nand";
$trans{NOR}       = "nor";
$trans{NOT}       = "not";
$trans{DFF}       = "dff";
$trans{DFF_PP0}   = "dff";
$trans{DFFE_PP0P} = "dffe";

$primitive{AND}   = 1;
$primitive{OR}    = 1;
$primitive{NAND}  = 1;
$primitive{NOR}   = 1;
$primitive{NOT}   = 1;

while (<>) {
    if (/\\\$_(\S*)_(\s.*)/) {
	if ($g = $trans{$1}) {
	    $_ = "  ".$g.$2;
	    if (! $primitive{$1}) {
		$need{$1} = 1;
	    }
	}  	 
    }
    print;
}

foreach $mod (keys %need) {
    
    print "`include \"../$mod.v\"\n";
}
