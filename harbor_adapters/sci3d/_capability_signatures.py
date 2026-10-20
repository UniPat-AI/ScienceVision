"""What each declared ``forbidden_tools`` capability looks like in code.

A sci3d task's ``forbidden_tools`` names a *capability* -- ``nifti_centroid_api``,
``plip_analyze`` -- not a tool. No tool with that name is ever called, so a
tool-name check can never enforce it. The capability is realised as a library
call inside a shell command or a written script, and this table is the mapping
from the declared name to the code that realises it.

Two severities, because one tier alone gets it wrong in opposite directions:

``VIOLATION``
    A signature that only exists to do the forbidden thing.
    ``scipy.ndimage.center_of_mass`` computes a centroid and nothing else.

``SUSPECT``
    A general-purpose primitive the forbidden thing is *usually* built from.
    ``np.nonzero`` is innocent on its own; over the staged ground-truth mask it
    is the shortcut. ``_capability_policy`` escalates a suspect to a violation
    when the same command also names a staged ``/data/`` path -- which is what
    catches "the agent ran numpy over the mask" without flagging numpy.

``UNENFORCEABLE_BY_SIGNATURE`` is the third state and the reason this file can
be trusted. Some declared capabilities describe *reading a value that was
staged* (``metadata_lookup_answer``) or a whole algorithm class with no library
fingerprint (``robot_self_occlusion_solver``). No regex finds those. They are
listed with a reason instead of being left absent, because absent is
indistinguishable from "nobody has written the rule yet", and both would report
as clean. ``audit_capability_policy`` counts them against coverage.

Corpus note: 60 distinct capability names appear across the shipped QA-Gen task
sets; the 11 highest-volume carry ~95% of all declarations and are mapped with
several signatures each. ``tests/harbor_adapters/test_capability_policy.py``
fails if any of those 11 loses its violation-level signature.

Patterns are regex sources matched case-insensitively against agent-authored
text. Keep them anchored on identifiers (``\\b``) rather than bare substrings:
an unanchored ``dock`` would match ``docker``.
"""
from __future__ import annotations

VIOLATION = "violation"
SUSPECT = "suspect"

# Primitives that appear as suspects under several capabilities. Named once so a
# change lands everywhere rather than in whichever entry someone remembered.
_NUMPY_MASK_PRIMITIVES: tuple[tuple[str, str], ...] = (
    (r"\bnp\.nonzero\b|\bnumpy\.nonzero\b|\.nonzero\(", SUSPECT),
    (r"\bnp\.argwhere\b|\bnumpy\.argwhere\b", SUSPECT),
    (r"\bnp\.where\b|\bnumpy\.where\b", SUSPECT),
    (r"\bget_fdata\b|\bdataobj\b", SUSPECT),
)
_NIFTI_READERS: tuple[tuple[str, str], ...] = (
    (r"\bnib\.load\b|\bnibabel\b", SUSPECT),
    (r"\bsitk\.ReadImage\b|\bSimpleITK\b", SUSPECT),
)
_STRUCTURE_READERS: tuple[tuple[str, str], ...] = (
    (r"\bBio\.PDB\b|\bBiopython\b", SUSPECT),
    (r"\bMDAnalysis\b|\bmdtraj\b|\bprody\b", SUSPECT),
    (r"\bgemmi\b", SUSPECT),
)
_RDKIT_READERS: tuple[tuple[str, str], ...] = (
    (r"\brdkit\b", SUSPECT),
    (r"\bChem\.MolFrom\w+", SUSPECT),
)
_NEIGHBOUR_SEARCH: tuple[tuple[str, str], ...] = (
    (r"\bcKDTree\b|\bKDTree\b", SUSPECT),
    (r"\bcdist\b|\bdistance_matrix\b", SUSPECT),
)

CAPABILITY_SIGNATURES: dict[str, tuple[tuple[str, str], ...]] = {
    # -- medical / NIfTI -------------------------------------------------- #
    "nifti_centroid_api": (
        (r"\bcenter_of_mass\b|\bcenterOfMass\b", VIOLATION),
        (r"\bLabelShapeStatisticsImageFilter\b|\bGetCentroid\b", VIOLATION),
        (r"\bregionprops\b", VIOLATION),
        (r"\bcentre_of_mass\b|\bcompute_centroid\b", VIOLATION),
        *_NUMPY_MASK_PRIMITIVES,
        *_NIFTI_READERS,
    ),
    "nibabel_header_lookup": (
        (r"\baff2axcodes\b|\bio_orientation\b|\bornt_transform\b", VIOLATION),
        (r"\bget_zooms\b|\bGetSpacing\b|\bGetDirection\b|\bGetOrigin\b", VIOLATION),
        (r"\.header\b|\bheader\[", VIOLATION),
        (r"\.affine\b|\bget_qform\b|\bget_sform\b", VIOLATION),
        *_NIFTI_READERS,
    ),
    "slice_index_api": (
        (r"\bget_slice_index\b|\bslice_index_api\b|\bfind_slice\b", VIOLATION),
        (r"\bnonzero\([^)]*\)\s*\[\s*[0-2]\s*\]", VIOLATION),
        (r"\bnp\.argmax\b|\bnumpy\.argmax\b|\.argmax\(", SUSPECT),
        (r"\bnp\.unique\b", SUSPECT),
        *_NUMPY_MASK_PRIMITIVES,
        *_NIFTI_READERS,
    ),
    "scipy_ndimage_label": (
        (r"\bndimage\.label\b|\bmeasure\.label\b|\bcc3d\b", VIOLATION),
        (r"\bfind_objects\b", VIOLATION),
        *_NIFTI_READERS,
    ),
    "volume_stats_api": (
        (r"\bLabelStatisticsImageFilter\b|\bGetPhysicalSize\b", VIOLATION),
        (r"\bregionprops\b|\bGetVolume\b", VIOLATION),
        (r"\bcount_nonzero\b|\.sum\(\)", SUSPECT),
        *_NUMPY_MASK_PRIMITIVES,
    ),
    "direct_mask_component_count": (
        (r"\bndimage\.label\b|\bmeasure\.label\b|\bcc3d\b", VIOLATION),
        (r"\bconnected_components\b", VIOLATION),
        *_NUMPY_MASK_PRIMITIVES,
    ),
    "direct_mask_bbox_solver": (
        (r"\bfind_objects\b|\bregionprops\b|\bGetBoundingBox\b", VIOLATION),
        (r"\bbounding_box\b|\bbbox\b", VIOLATION),
        *_NUMPY_MASK_PRIMITIVES,
    ),
    "boolean_mask_overlap_solver": (
        (r"\bjaccard\b|\bdice(_score|_coefficient)?\b", VIOLATION),
        (r"\blogical_and\b|\blogical_or\b|\bintersect1d\b", SUSPECT),
        *_NUMPY_MASK_PRIMITIVES,
    ),
    # -- protein / structure ---------------------------------------------- #
    "pdb_contact_table": (
        (r"\bNeighborSearch\b|\bneighbor_search\b", VIOLATION),
        (r"\bcontact_map\b|\bcontacts?_table\b|\bget_contacts\b", VIOLATION),
        (r"\bcapped_distance\b|\bself_capped_distance\b", VIOLATION),
        *_NEIGHBOUR_SEARCH,
        *_STRUCTURE_READERS,
    ),
    "clash_score_api": (
        (r"\bclash_?score\b|\bmolprobity\b|\bprobe\s+-", VIOLATION),
        (r"\bvdw_overlap\b|\bsteric_clash\b", VIOLATION),
        *_NEIGHBOUR_SEARCH,
        *_STRUCTURE_READERS,
    ),
    "plip_analyze": (
        (r"\bplip\b|\bPLIPComplex\b|\bplipcmd\b", VIOLATION),
        (r"\bprolif\b|\boddt\b|\binteraction_fingerprint\b", SUSPECT),
        *_STRUCTURE_READERS,
    ),
    "plip_detect": (
        (r"\bplip\b|\bPLIPComplex\b|\bplipcmd\b", VIOLATION),
        (r"\bprolif\b|\boddt\b", SUSPECT),
    ),
    "interaction_fingerprint_api": (
        (r"\bprolif\b|\binteraction_fingerprint\b|\bIFP\b", VIOLATION),
        (r"\bplip\b|\boddt\b", VIOLATION),
    ),
    "hbond_detect_api": (
        (r"\bh(ydrogen)?_?bonds?\b|\bHydrogenBondAnalysis\b", VIOLATION),
        (r"\bbaker_hubbard\b|\bwernet_nilsson\b", VIOLATION),
        *_STRUCTURE_READERS,
    ),
    "pocket_detection_api": (
        (r"\bfpocket\b|\bPocketFinder\b|\bgetcleft\b|\bsitemap\b", VIOLATION),
        (r"\bpocket(s)?_?detect\w*\b", VIOLATION),
    ),
    "docking_score_api": (
        (r"\bvina\b|\bsmina\b|\bgnina\b|\bAutoDock\b", VIOLATION),
        (r"\bdocking_score\b|\bscore_only\b", VIOLATION),
    ),
    "direct_coordinate_file_read": (
        (r"\bMolFromPDBFile\b|\bMolFromMol2File\b|\bMolFromMolFile\b", VIOLATION),
        (r"\bPDBParser\b|\bMMCIFParser\b|\bread_structure\b", VIOLATION),
        (r"\bopen\([^)]*\.(pdb|cif|mmcif|mol2|sdf|xyz)\b", VIOLATION),
        *_STRUCTURE_READERS,
    ),
    # -- crystallography / CIF -------------------------------------------- #
    "gemmi_spacegroup_lookup": (
        (r"\bspace_?group\b|\bfind_spacegroup\b", VIOLATION),
        (r"\b_symmetry_space_group\w*\b|\bhall_symbol\b|\bhm\b", VIOLATION),
        (r"\bSpacegroupAnalyzer\b|\bget_space_group\w*\b", VIOLATION),
        (r"\bCifFile\b|\bpymatgen\b|\base\.io\b", SUSPECT),
        *_STRUCTURE_READERS,
    ),
    "cif_read_symmetry": (
        (r"\b_symmetry_equiv_pos\w*\b|\b_space_group_symop\w*\b", VIOLATION),
        (r"\bsymmetry_operations\b|\bsym_?ops\b|\bget_symops\b", VIOLATION),
        (r"\bSpacegroupAnalyzer\b|\bspacegroup\.operations\b", VIOLATION),
        (r"\bCifFile\b|\bpymatgen\b|\base\.io\b", SUSPECT),
        *_STRUCTURE_READERS,
    ),
    "symmetry_expansion_api": (
        (r"\bexpand_to_p1\b|\bexpand_structure\b|\bgenerate_symmetry\w*\b", VIOLATION),
        (r"\bapply_symmetry\b|\bmake_p1\b", VIOLATION),
        (r"\bgemmi\b|\bpymatgen\b", SUSPECT),
    ),
    # -- small molecule / RDKit ------------------------------------------- #
    "rdkit_assign_stereo": (
        (r"\bAssignStereochemistry\b|\bAssignCIPLabels\b", VIOLATION),
        (r"\bFindMolChiralCenters\b|\bAssignAtomChiralTagsFromStructure\b", VIOLATION),
        (r"\bCIPLabeler\b|\bFindPotentialStereo\b", VIOLATION),
        *_RDKIT_READERS,
    ),
    "cip_assignment_api": (
        (r"\bAssignCIPLabels\b|\bCIPLabeler\b|\bAssignStereochemistry\b", VIOLATION),
        (r"\bFindMolChiralCenters\b", VIOLATION),
        *_RDKIT_READERS,
    ),
    "smiles_to_conformer": (
        (r"\bEmbedMolecule\b|\bEmbedMultipleConfs\b", VIOLATION),
        (r"\bobabel\b.*--gen3d|\bgen3[dD]\b|\bmake3D\b", VIOLATION),
        (r"\bETKDG\b|\bMMFFOptimizeMolecule\b|\bUFFOptimizeMolecule\b", VIOLATION),
        *_RDKIT_READERS,
    ),
    "smiles_to_conformer_api": (
        (r"\bEmbedMolecule\b|\bEmbedMultipleConfs\b|\bETKDG\b", VIOLATION),
        (r"\bobabel\b.*--gen3d|\bgen3[dD]\b|\bmake3D\b", VIOLATION),
        *_RDKIT_READERS,
    ),
    "rdkit_calc_dihedral": (
        (r"\bGetDihedralDeg\b|\bGetDihedralRad\b|\bSetDihedral\w*\b", VIOLATION),
        (r"\bCalcDihedral\b|\bcalc_dihedral\b|\bcompute_dihedrals\b", VIOLATION),
        (r"\bdihedrals?\b|\btorsions?\b", SUSPECT),
        *_RDKIT_READERS,
    ),
    # -- mesh / geometry introspection ------------------------------------ #
    "trimesh": (
        (r"\bimport\s+trimesh\b|\btrimesh\.(load|Trimesh|Scene)\b", VIOLATION),
    ),
    "pygltflib": (
        (r"\bimport\s+pygltflib\b|\bGLTF2\(\)?\.?load\w*\b|\bfrom\s+pygltflib\b", VIOLATION),
    ),
    "any_glb_parser": (
        (r"\bimport\s+trimesh\b|\bimport\s+pygltflib\b|\bGLTF2\b", VIOLATION),
        (r"\bpyassimp\b|\bopen3d\b|\bgltflib\b|\bplyfile\b", VIOLATION),
    ),
    "any_geometry_introspection": (
        (r"\bimport\s+trimesh\b|\bimport\s+pygltflib\b|\bopen3d\b|\bGLTF2\b", VIOLATION),
        (r"\.vertices\b|\.faces\b|\bscene\.geometry\b|\.nodes\b", SUSPECT),
    ),
    "read_mesh_node_count": (
        (r"\bscene\.geometry\b|\.nodes\b|\blen\(\s*\w*\.?nodes", VIOLATION),
        (r"\bimport\s+trimesh\b|\bimport\s+pygltflib\b|\bGLTF2\b", SUSPECT),
    ),
    "read_instance_count": (
        (r"\bscene\.graph\b|\.instances?\b|\bduplicate_nodes\b", VIOLATION),
        (r"\bimport\s+trimesh\b|\bimport\s+pygltflib\b|\bGLTF2\b", SUSPECT),
    ),
    "get_vertices": (
        (r"\bget_vertices\b|\.vertices\b", VIOLATION),
        (r"\bimport\s+trimesh\b|\bopen3d\b", SUSPECT),
    ),
    "raycast_visibility_solver": (
        (r"\braycast\b|\bray_triangle\w*\b|\bintersects_location\b", VIOLATION),
        (r"\bembree\b|\bRayMeshIntersector\b", VIOLATION),
    ),
    "curve_fit_gap_solver": (
        (r"\bcurve_fit\b|\bpolyfit\b|\binterp1d\b|\bUnivariateSpline\b", VIOLATION),
    ),
    "direct_graph_topology_api": (
        (r"\bimport\s+networkx\b|\bnx\.\w+", VIOLATION),
        (r"\bconnected_components\b|\bcsgraph\b", VIOLATION),
    ),
    "connected_components": (
        (r"\bconnected_components\b|\bcsgraph\b|\bndimage\.label\b", VIOLATION),
    ),
    "connected_components_pointcloud": (
        (r"\bconnected_components\b|\bcluster_dbscan\b|\bDBSCAN\b", VIOLATION),
        (r"\bopen3d\b|\bsklearn\.cluster\b", SUSPECT),
    ),
    "dbscan": (
        (r"\bDBSCAN\b|\bdbscan\(|\bcluster_dbscan\b", VIOLATION),
        (r"\bsklearn\.cluster\b", SUSPECT),
    ),
    # -- physics / simulator state ---------------------------------------- #
    # Genesis exposes these as literal method names, so the signature is exact.
    "any_physics_simulator": (
        (r"\bimport\s+genesis\b|\bimport\s+pybullet\b|\bimport\s+mujoco\b", VIOLATION),
        (r"\bisaacgym\b|\bsapien\b|\bpymunk\b|\bBox2D\b|\btaichi\b", VIOLATION),
        (r"\bgs\.(init|Scene)\b|\bp\.connect\b", VIOLATION),
    ),
    "get_particles_pos": ((r"\bget_particles_pos\b", VIOLATION),),
    "get_particles_active": ((r"\bget_particles_active\b", VIOLATION),),
    "get_pos": ((r"\bget_pos\s*\(", VIOLATION),),
    "get_quat": ((r"\bget_quat\s*\(", VIOLATION),),
    "get_state": ((r"\bget_state\s*\(", VIOLATION),),
    "get_dofs_force": ((r"\bget_dofs_force\b", VIOLATION),),
    "get_contacts": ((r"\bget_contacts\b", VIOLATION),),
}

# Declared capabilities that no static signature can decide, with the reason.
# These count against ``coverage`` rather than being silently absent: absent is
# indistinguishable from "the rule was never written", and both read as clean.
UNENFORCEABLE_BY_SIGNATURE: dict[str, str] = {
    "metadata_lookup_answer":
        "reading a value that was staged alongside the asset; the code is a "
        "plain file read with no fingerprint. Fixed by not staging it (T1), "
        "not by scanning for it.",
    "staging_metadata_lookup":
        "same as metadata_lookup_answer: the defect is that the value is "
        "reachable at all, which is a staging decision (T1).",
    "direct_mask_text_lookup":
        "the answer is present as text beside the mask; reading it is an "
        "ordinary file read. Detectable only by not staging the text (T1).",
    "segmentation_mask_lookup":
        "reading the staged segmentation rather than computing over it; "
        "indistinguishable from a legitimate read of the same path.",
    "slice_index_lookup":
        "reading a staged slice index; a file read with no library signature.",
    "smiles_metadata_lookup":
        "reading the SMILES string from staged metadata; a plain read.",
    "rmsd_to_native_lookup":
        "reading a precomputed RMSD value; a plain read.",
    "direct_ephemeris_answer_lookup":
        "reading a precomputed ephemeris value; a plain read.",
    "robot_self_occlusion_solver":
        "names an algorithm class with no library fingerprint -- any forward "
        "kinematics plus depth test qualifies, and none of it is distinctive.",
    "kinematic_graph_classifier":
        "names an algorithm class; a hand-rolled joint-graph walk uses no "
        "identifiable API.",
    "building_footprint_zbuffer_solver":
        "names an algorithm class; a hand-rolled z-buffer projection uses "
        "only array arithmetic.",
}
