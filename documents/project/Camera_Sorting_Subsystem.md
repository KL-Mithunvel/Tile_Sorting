Camera_Sorting_Subsystem
the camera stage can act as the **visual inspection + counting + logging station**.

**First station in the line (decision, 2026-07-10):** the camera is positioned before
acoustic and dimensional inspection. It is the point where the master first learns a
tile exists — on detecting a tile it reports presence + its visual result to the master,
which opens that tile's record (see `project_charter.md` §6.1 "Role as First Station",
`Automation_Architecture.md` §2/§4). It also owns the running tile count, and can supply
a secondary, cross-check belt-speed estimate from frame-to-frame tile motion — a
dedicated conveyor encoder remains the authoritative speed/position reference.

Use it for:

1. **Major defect detection**

   * Large visible cracks
   * Broken/chipped corners
   * Missing edge portions
   * Major surface damage

2. **Shape/profile inspection**

   * Confirm tile outline is close to expected rectangle/square
   * Detect warped outline, missing corners, broken edges
   * Compare measured contour against reference tile shape

3. **Counting**

   * Increment `total_tiles_passed`
   * Count accepted/rejected tiles
   * Count defect types separately:

     * `crack_count`
     * `corner_break_count`
     * `shape_defect_count`

4. **Data entry/logging**
   Each tile can get one row like:

| Field                 | Example             |
| --------------------- | ------------------- |
| tile_id               | TILE_000245         |
| timestamp             | 2026-05-25 14:32:10 |
| camera_status         | OK                  |
| visual_grade          | Reject / Pass       |
| crack_detected        | Yes/No              |
| corner_break_detected | Yes/No              |
| shape_score           | 92%                 |
| acoustic_grade        | A/B/C               |
| dimension_grade       | Pass/Fail           |
| final_grade           | A / B / Reject      |
| daily_tile_count      | 245                 |

For the camera system, I would use:

**Camera:** Raspberry Pi HQ Camera / Arducam USB camera / industrial USB camera
**Lighting:** Fixed LED bar or ring light, preferably diffused
**Compute:** Raspberry Pi 5 for basic OpenCV, or mini PC/Jetson if using AI defect detection
**Software:** OpenCV first, YOLO/segmentation model later if needed

Basic visual pipeline:

```text
Tile enters inspection area
↓
Trigger sensor detects tile
↓
Camera captures image
↓
OpenCV extracts tile boundary/contour
↓
Check corners, edges, visible cracks
↓
Save image + result
↓
Increment daily count
↓
Send grade to sorting controller
```

Best practical method: start with **OpenCV contour + edge inspection**, then later add **AI model detection** if normal image processing is not enough.
