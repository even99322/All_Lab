"""
YIG (鐵磁共振球) 相關的物理換算與 AEDT 指令產生。

AEDT 指令格式取自 AEDT 2026.1 錄製的腳本：
    oDefinitionManager.AddMaterial / EditMaterial   (saturation_mag, delta_H, delta_H_freq ...)
    oModule("BoundarySetup").AssignMagneticBias     (Objects, IsUniformBias, Bias[A/m], X/Y/ZAngle)
"""
import math

MU0 = 4e-7 * math.pi                 # H/m
GAMMA_PER_G = 13.99624493           # GHz/T per unit g  (γ/2π = g·μB/h)

YIG_MATERIAL = "YIG_auto"
YIG_OBJECT = "YIG_Sphere"
YIG_BIAS = "YIG_Bias"
YIG_MESH = "YIG_Mesh"
YIG_CURV = "YIG_Curvilinear"
KITTEL_SWEEP = "Sweep_Kittel"

# v3.6.1：球體網格。HFSS 依「最大邊長」在球內預先撒點（Seed Refinement）；邊長太小會一次長到幾十萬個四面體而記憶體不夠。
# 經驗式：四面體數 ≈ 37 × 體積 / 邊長³（對照 AEDT「Seed Refinement Estimates」：⌀1.2 mm、0.05 mm → 約 27 萬）。
MESH_WARN_TETS = 40_000
MESH_MAX_ELEMENTS = 30_000      # 網格設定的「最多加幾個元素」上限（Restrict Max Elements），避免預先撒點把記憶體吃光


def auto_mesh_mm(diameter_mm):
    """沒填網格邊長時：直徑 / 8（⌀1.2 mm → 0.15 mm，約一萬個四面體）。"""
    return round(max(diameter_mm, 1e-3) / 8, 4)


def mesh_estimate(diameter_mm, edge_mm):
    if not diameter_mm or not edge_mm or edge_mm <= 0:
        return 0
    vol = math.pi / 6 * diameter_mm ** 3
    return int(37.4 * vol / edge_mm ** 3)


def mesh_for_tets(diameter_mm, tets):
    """要讓球裡大約 tets 個四面體的邊長（建議值）。"""
    vol = math.pi / 6 * diameter_mm ** 3
    return round((37.4 * vol / max(tets, 1)) ** (1 / 3), 3)

# 偏置方向 → AEDT 的旋轉角 (預設偏置沿 +Z；繞 X 轉 -90° 得 +Y，繞 Y 轉 +90° 得 +X)
BIAS_DIRECTIONS = {
    "+Z (垂直板面)": ("0deg", "0deg", "0deg"),
    "+X": ("0deg", "90deg", "0deg"),
    "+Y": ("-90deg", "0deg", "0deg"),
}


def gamma_ghz_per_t(g=2.0):
    return g * GAMMA_PER_G


def bias_fields(value, mode, sat_mag_t, g=2.0):
    """由使用者輸入計算各種偏置場表示。

    mode = 'external_mT' : value 是外加磁場 μ0·H_ext (mT)，球體內部場 H_int = H_ext − Ms/3
    mode = 'internal_Apm': value 直接是 HFSS 的 Bias (A/m，視為內部場)
    回傳 dict: H_int (A/m), B_ext (mT), f_kittel (GHz)
    """
    ms = sat_mag_t / MU0                      # A/m  (sat_mag_t = μ0·Ms，例如 0.175 T)
    if mode == "external_mT":
        h_ext = value * 1e-3 / MU0
        h_int = h_ext - ms / 3.0
    else:
        h_int = float(value)
        h_ext = h_int + ms / 3.0
    b_ext_t = MU0 * h_ext
    # 球體 Kittel 模式：ω = γ μ0 (H_int + Ms/3) = γ μ0 H_ext
    f_k = gamma_ghz_per_t(g) * b_ext_t
    return {"H_int": h_int, "H_ext": h_ext, "B_ext_mT": b_ext_t * 1e3, "f_kittel": f_k, "Ms": ms}


def linewidth_mhz(delta_h_oe, g=2.0):
    """FMR 半高全寬 (MHz) ≈ γ · μ0ΔH ；1 Oe = 0.1 mT"""
    return gamma_ghz_per_t(g) * (delta_h_oe * 1e-4) * 1e3


# v3.7：文獻參數（YIG 單晶球；見 YIG 參數說明視窗「文獻參數」）
#   室溫：Maier-Flaig et al., PRB 95, 214423 (2017)：μ0Ms(300 K) = 180.0 mT、g = 2.0054、α ≈ 4×10⁻⁵
#   低溫：同一篇 Fig. 3 / 4：μ0Ms(10 K) ≈ 248 mT、g ≈ 2.010、9.6 GHz 線寬 ≈ 1.4 MHz（雜質主導，幾乎不隨頻率變）
#   εr、tanδ 不在這幾篇裡：用常見的 YIG 值 15、2×10⁻⁴
PRESETS = {
    "room": {"label": "室溫 300 K（Maier-Flaig 2017）", "ms": 0.180, "g": 2.005, "dh": 0.27, "dhf": 9.4, "er": 15.0, "tand": 2e-4},
    "cold": {"label": "低溫 ≈10 K（Maier-Flaig 2017）", "ms": 0.248, "g": 2.010, "dh": 0.50, "dhf": None, "er": 15.0, "tand": 2e-4},
}


def gilbert_dh_oe(alpha, f_ghz, g=2.0):
    """Gilbert 阻尼 → 場線寬（半高全寬）：Δf = 2αf、ΔH = Δf / (γ/2π)。"""
    return 2 * alpha * f_ghz * 1e3 / (gamma_ghz_per_t(g) * 1e3 * 1e-4)   # MHz ÷ (MHz/Oe)


def material_args(name, er, tand, sat_mag_t, delta_h_oe, delta_h_freq_ghz=None, g=None):
    args = [
        f"NAME:{name}",
        "CoordinateSystemType:=", "Cartesian",
        "BulkOrSurfaceType:=", 1,
        ["NAME:PhysicsTypes", "set:=", ["Electromagnetic"]],
        "permittivity:=", f"{er:g}",
        "dielectric_loss_tangent:=", f"{tand:g}",
        "saturation_mag:=", f"{sat_mag_t:g}tesla",
        "delta_H:=", f"{delta_h_oe:g}Oe",
        "mass_density:=", "5170",
    ]
    if delta_h_freq_ghz:
        args += ["delta_H_freq:=", f"{delta_h_freq_ghz:g}GHz"]
    if g is not None:                     # v3.7：HFSS 的 Landé g（預設 2）要跟算 Kittel 用的一樣
        args += ["lande_G_factor:=", f"{g:g}"]
    return args


def bias_args(obj_name, h_int_apm, direction):
    xa, ya, za = BIAS_DIRECTIONS.get(direction, BIAS_DIRECTIONS["+Z (垂直板面)"])
    return [
        f"NAME:{YIG_BIAS}",
        "Objects:=", [obj_name],
        "IsUniformBias:=", True,
        "Bias:=", f"{h_int_apm:.6g}",
        "XAngle:=", xa,
        "YAngle:=", ya,
        "ZAngle:=", za,
    ]


def set_material_args(obj_name, material):
    return ["NAME:AllTabs", ["NAME:Geometry3DAttributeTab", ["NAME:PropServers", obj_name],
                             ["NAME:ChangedProps", ["NAME:Material", "Value:=", f'"{material}"']]]]


def sphere_center(params, guide_bbox):
    """球心座標 (mm)。guide_bbox = [xmin, ymin, zmin, xmax, ymax, zmax] (所有訊號線的聯集) 或 None。"""
    r = params["yig_diameter"] / 2.0
    z_top = params["thick_die"] + params["thick_top"]             # 訊號線頂面
    z = z_top + params["yig_gap"] + r
    if params.get("yig_pos_mode", "auto") == "auto" and guide_bbox:
        x = (guide_bbox[0] + guide_bbox[3]) / 2 + params.get("yig_dx", 0.0)
        y = (guide_bbox[1] + guide_bbox[4]) / 2 + params.get("yig_dy", 0.0)
    else:
        x, y = params.get("yig_x", 0.0), params.get("yig_y", 0.0)
    return [x, y, z], r
