// ZEN16 keyhole adapter plate for Carlon E987R 6x6x4 PVC box
// Hangs the ZEN16 on two mushroom studs; the box's back-wall mounting screws
// pass through countersunk holes in this plate (plate is clamped between
// screw heads... no — screws countersink INTO the plate, clamping plate+box
// to the wall; ZEN16 hangs on the studs above them).
//
// ALL DIMENSIONS ARE PLACEHOLDERS — verify with calipers on the actual ZEN16
// before printing. Print flat side down, no supports; PETG or PLA fine.

/* ---------------- measure these ---------------- */
keyhole_spacing   = 76.0;  // ZEN16 keyhole center-to-center, mm (MEASURE!)
slot_width        = 4.0;   // narrow part of keyhole slot -> stud neck dia
entry_hole_dia    = 8.0;   // round part of keyhole -> stud head must be < this
backplate_thick   = 2.0;   // ZEN16 shell thickness at the keyhole
wall_screw_space  = 40.0;  // spacing of the two box-mounting screws, mm
wall_screw_dia    = 4.0;   // #6 screw shank clearance
wall_screw_head   = 8.5;   // #6 pan/flat head dia for countersink

/* ---------------- derived / tunable ---------------- */
plate_len   = keyhole_spacing + 20;
plate_wid   = 22;
plate_thick = 3;
neck_dia    = slot_width - 0.4;      // slides in slot with clearance
neck_len    = backplate_thick + 0.4; // shell thickness + slip clearance
head_dia    = entry_hole_dia - 0.6;  // fits through entry hole
head_thick  = 2.0;

$fn = 48;

difference() {
    union() {
        // plate
        cube([plate_len, plate_wid, plate_thick], center = false);
        // two mushroom studs
        for (x = [plate_len/2 - keyhole_spacing/2,
                  plate_len/2 + keyhole_spacing/2])
            translate([x, plate_wid/2, plate_thick]) {
                cylinder(d = neck_dia, h = neck_len);
                translate([0, 0, neck_len])
                    cylinder(d = head_dia, h = head_thick);
            }
    }
    // countersunk wall-screw holes, centered between the studs
    for (x = [plate_len/2 - wall_screw_space/2,
              plate_len/2 + wall_screw_space/2])
        translate([x, plate_wid/2, -0.1]) {
            cylinder(d = wall_screw_dia, h = plate_thick + 0.2);
            // countersink from the front face
            translate([0, 0, plate_thick - 1.5])
                cylinder(d1 = wall_screw_dia, d2 = wall_screw_head, h = 1.6);
        }
}
