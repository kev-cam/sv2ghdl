-- 3D Logic Package for ATPG simulation
-- Re-exports types and functions from logic3d_types_pkg

library work;
use work.logic3d_types_pkg.all;

package logic3d_pkg is

    -- Re-export the enum type and constants
    subtype logic3d is work.logic3d_types_pkg.logic3d;

    constant L3D_0 : logic3d := work.logic3d_types_pkg.L3D_0;
    constant L3D_1 : logic3d := work.logic3d_types_pkg.L3D_1;
    constant L3D_Z : logic3d := work.logic3d_types_pkg.L3D_Z;
    constant L3D_X : logic3d := work.logic3d_types_pkg.L3D_X;

    -- Re-export gate functions
    alias l3d_not is work.logic3d_types_pkg.l3d_not[logic3d return logic3d];
    alias l3d_and is work.logic3d_types_pkg.l3d_and[logic3d, logic3d return logic3d];
    alias l3d_or is work.logic3d_types_pkg.l3d_or[logic3d, logic3d return logic3d];
    alias l3d_xor is work.logic3d_types_pkg.l3d_xor[logic3d, logic3d return logic3d];
    alias l3d_nand is work.logic3d_types_pkg.l3d_nand[logic3d, logic3d return logic3d];
    alias l3d_nor is work.logic3d_types_pkg.l3d_nor[logic3d, logic3d return logic3d];
    alias l3d_xnor is work.logic3d_types_pkg.l3d_xnor[logic3d, logic3d return logic3d];
    alias l3d_buf is work.logic3d_types_pkg.l3d_buf[logic3d return logic3d];

    -- Multi-input gates
    alias l3d_and3 is work.logic3d_types_pkg.l3d_and3[logic3d, logic3d, logic3d return logic3d];
    alias l3d_and4 is work.logic3d_types_pkg.l3d_and4[logic3d, logic3d, logic3d, logic3d return logic3d];
    alias l3d_or3 is work.logic3d_types_pkg.l3d_or3[logic3d, logic3d, logic3d return logic3d];
    alias l3d_or4 is work.logic3d_types_pkg.l3d_or4[logic3d, logic3d, logic3d, logic3d return logic3d];
    alias l3d_xor3 is work.logic3d_types_pkg.l3d_xor3[logic3d, logic3d, logic3d return logic3d];
    alias l3d_nand3 is work.logic3d_types_pkg.l3d_nand3[logic3d, logic3d, logic3d return logic3d];
    alias l3d_nor3 is work.logic3d_types_pkg.l3d_nor3[logic3d, logic3d, logic3d return logic3d];

    -- Re-export utilities
    alias to_char is work.logic3d_types_pkg.to_char[logic3d return character];
    alias is_one is work.logic3d_types_pkg.is_one[logic3d return boolean];
    alias is_zero is work.logic3d_types_pkg.is_zero[logic3d return boolean];
    alias is_x is work.logic3d_types_pkg.is_x[logic3d return boolean];
    alias is_z is work.logic3d_types_pkg.is_z[logic3d return boolean];
    alias is_strong is work.logic3d_types_pkg.is_strong[logic3d return boolean];

end package;
