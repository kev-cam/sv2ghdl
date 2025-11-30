#!/usr/bin/env perl

$trans{AND}       = "and";
$trans{OR}        = "or";
$trans{NAND}      = "nand";
$trans{NOR}       = "nor";
$trans{NOT}       = "not";
$trans{DFF}       = "dff";
$trans{DFF_PP0}   = "dff";
$trans{DFFE_PP0P} = "dffe";

while (<>) {
    if (/\\\$_(\S*)_(\s.*)/) {
	if ($g = $trans{$1}) {
	    $_ = "  ".$g.$2;
	    $need{$1} = 1;
	}  	 
    }
    print;
}
