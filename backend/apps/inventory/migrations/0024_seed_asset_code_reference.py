"""依据 11.2 市场+研发库存明细建立 V2 编码基础资料。

数据来源：`11.2市场+研发库存明细.xlsx`（市场库存 + 研发库存两个工作表，
共 69 个型号、37 个厂家、12 个设备类型）。

设计说明：

- 设备类型按品类归并，不照抄产品名称，避免每个名称都变成独立类型。
- 厂家名称已做别名归并（如“宇树”统一为“宇树科技”、“视元”统一为“视元智能”）。
- 型号码由厂家型号规范化而来，同一型号的多种写法已合并
  （如 `335` 与 `ORBBEC Gemini 335` 合并、`4目全景` 与 `SY-4MU` 合并）。
- 未登记的中文厂家名用稳定哈希生成 `MF` 前缀代码，避免多个厂家塌缩成同一代码。
- 这些是**初始数据**，不是硬编码规则：仓管可在后台新增、编辑、停用。
  已生成过资产编码的型号受保护，不可改码、不可删除。
- 市场侧仅 1 行（HUAWEI Pad）缺型号，会阻塞其本身编码生成，需补录。
- 研发侧 16 行型号缺失或为描述性文字（如“展老师手搓”“热成像三轴云台”），
  但研发资产走 `DZSB-` 行政编码，不依赖型号，不构成阻塞。
"""

from django.db import migrations


DEVICE_TYPES = [
    ('CD', '充电设备'),
    ('DZ', '电子模块'),
    ('JGJ', '结构加工件'),
    ('JQR', '机器人整机'),
    ('LD', '激光雷达'),
    ('MIC', '麦克风/音响'),
    ('QT', '其他'),
    ('SB', '仪器设备'),
    ('SZ', '灵巧手/夹爪'),
    ('XJ', '相机模组'),
    ('YT', '云台吊舱'),
    ('ZJ', '主机/计算单元'),
]

MANUFACTURERS = [
    ('AO', '艾欧智能'),
    ('BAMBU', '拓竹'),
    ('CY', '承已'),
    ('DB', '当贝'),
    ('DH', '大华'),
    ('DJAM', '迪吉艾牧'),
    ('DLHG', '大连汇高'),
    ('DM', '东茂工业'),
    ('HIK', '海康威视'),
    ('HS', '环视智能'),
    ('HT', '海塔'),
    ('HY', '汇云'),
    ('JBL', 'JBL'),
    ('KC', '科聪'),
    ('LC', '浪潮'),
    ('LV', '览沃科技'),
    ('LY', '龙远'),
    ('MM', '猛犸'),
    ('NV', '英伟达'),
    ('OB', '奥比中光'),
    ('PICO', 'PICO'),
    ('QN', '强脑科技'),
    ('REM', '睿尔曼'),
    ('RS', '速腾聚创'),
    ('RTK', 'RTK'),
    ('SD', '闪迪'),
    ('SY', '视元智能'),
    ('TM', '武汉天眸光电'),
    ('UNIT', '宇树科技'),
    ('VITA', '维他动力'),
    ('WF', '蔚弗数采'),
    ('XF', '先飞'),
    ('YDZX', '南京一度智芯'),
    ('YS', '因时'),
    ('YZ', '云卓'),
    ('ZHANYUN', '瞻云'),
    ('ZY', '智元'),
    # 11.2 台账新出现的厂家，0024 初版遗漏，导入预检会报“生产厂家不存在”
    ('BWT', '八维通'),
    ('HUAWEI', '华为'),
    ('MLD', '长沙迈乐迪'),
    ('TR', '图瑞'),
    ('XDF', '祥达丰'),
]

# (设备类型代码, 厂家代码, 型号代码, 型号名称)
PRODUCT_MODELS = [
    ('CD', 'UNIT', 'A2', 'A2充电板'),
    ('CD', 'UNIT', 'B2', 'B2充电板'),
    ('CD', 'UNIT', 'G1', 'G1电池'),
    ('CD', 'UNIT', 'GO2', 'Go2充电桩'),
    ('DZ', 'DM', 'EVS01', 'EVS01'),
    ('DZ', 'RTK', 'UM960', 'UM960'),
    ('DZ', 'RTK', 'UM982', 'UM982'),
    ('DZ', 'YDZX', 'YDZX12224030M', 'YDZX-1222-40-30M'),
    ('DZ', 'YDZX', 'YDZX12224030S', 'YDZX-1222-40-30S'),
    ('JGJ', '', 'B2', 'B2云台保护架'),
    ('JGJ', 'REM', 'RML636FV', 'RML63-6F-V'),
    ('JQR', 'BAMBU', 'H2DPRO', 'H2D pro'),
    ('JQR', 'UNIT', 'A2PRO', 'A2 pro'),
    ('JQR', 'UNIT', 'A2W', 'A2W'),
    ('JQR', 'UNIT', 'AS2', 'AS2'),
    ('JQR', 'UNIT', 'AS2U1', 'As2-U1'),
    ('JQR', 'UNIT', 'AS2W', 'As2-W-标准版'),
    ('JQR', 'UNIT', 'B2', 'B2'),
    ('JQR', 'UNIT', 'B2W', 'B2-W'),
    ('JQR', 'UNIT', 'G1', 'G1'),
    ('JQR', 'UNIT', 'G1D', 'G1-D'),
    ('JQR', 'UNIT', 'G1EDU', 'G1-EDU+'),
    ('JQR', 'UNIT', 'GO2EDU', 'go2-EDU'),
    ('JQR', 'UNIT', 'H2', 'H2'),
    ('JQR', 'ZY', 'X2', '灵犀X2'),
    ('LD', 'LV', 'LIVOXMID360S', 'Livox MID-360S'),
    ('LD', 'RS', 'HELIOS32', 'Helios 32（H32F70）'),
    ('LD', 'TM', 'TM120', 'TM120'),
    ('MIC', 'JBL', 'GOESSENTIAL2', 'GO ESSENTIAL 2'),
    ('MIC', 'MM', 'LARKA1', 'LARK A1'),
    ('QT', 'AO', 'BASELINESOLUTIONI', 'Baseline Solution I（UMI版）'),
    ('QT', 'DJAM', 'NPW70ZS1DK06RSNM15', 'NPW70Z-S1-D-K06-RS-N-M1.5'),
    ('QT', 'LY', 'A10', 'A10'),
    ('QT', 'LY', 'A20', 'A20'),
    ('QT', 'VITA', 'VITADO11VBOT', 'VITADO11Vbot'),
    ('QT', 'WF', 'WFMIC128', 'WF-MIC128'),
    ('QT', 'YZ', 'C14PRO', 'C14pro'),
    ('SB', 'DB', 'X7ULTRA', 'X7 Ultra'),
    ('SB', 'PICO', 'PICO4ULTRAENTERPRISE', 'PICO4 Ultra Enterprise'),
    ('SB', 'SD', 'E814T', 'E81-4T'),
    # 11.2 台账里型号列空着或只写了描述性文字，按产品名补建型号，
    # 否则这些资产拿不到 SC 编码。
    ('SB', 'HUAWEI', 'PAD', 'HUAWEI Pad'),
    ('DZ', 'CY', 'GAS4', '气体传感器 四合一'),
    ('DZ', 'CY', 'GAS6', '气体传感器 六合一'),
    ('SZ', 'QN', 'GLOVE', '强脑灵巧手'),
    ('SZ', 'QN', 'BCREVO2', 'BC-Revo-2'),
    ('SZ', 'QN', 'REVO2', 'Revo 2左手'),
    ('SZ', 'UNIT', 'DEX11', 'dex1-1'),
    ('SZ', 'YS', 'RH56DFX2R', 'RH56DFX-2R'),
    ('SZ', 'YS', 'RH56E22LT1', 'RH56E2-2L-T1'),
    ('SZ', 'YS', 'RH56E22RT1', 'RH56E2-2R-T1'),
    ('XJ', 'HS', 'ORBVI4V', 'Orbvi 4V'),
    ('XJ', 'HS', 'ORBVI4VL', 'Orbvi 4VL'),
    ('XJ', 'HS', 'ORBVI4VLEDU', 'Orbvi 4VL EDU'),
    ('XJ', 'OB', '336L', '336L'),
    ('XJ', 'OB', 'ORBBECGEMINI335', 'ORBBEC Gemini 335'),
    ('XJ', 'SY', 'SY4MU', 'SY-4MU'),
    ('XJ', 'SY', 'SY4MUIR', 'SY-4MU-IR'),
    ('XJ', 'SY', 'SYOMNID', 'SY-OMNI-D'),
    ('YT', 'HIK', 'HMTD5567T15WGLT', 'HM-TD5567T-15/W/GLT'),
    ('YT', 'HIK', 'HMTD5737T7W', 'HM-TD5737T-7/W'),
    ('YT', 'HY', 'HYD330F', 'HY-D330F'),
    ('YT', 'XF', 'Z1PRO', '先飞Z-1Pro'),
    ('YT', 'YZ', 'C12', 'C12'),
    ('YT', 'YZ', 'C13', 'C13'),
    ('YT', 'YZ', 'C14PRO', 'C14pro'),
    ('YT', 'ZHANYUN', 'ZTD0958K', 'ZTD095 8K超清红外双光云台吊舱'),
    ('ZJ', 'HT', 'I913900HES32G512G', 'I9-13900H ES/32G+512G'),
    ('ZJ', 'LC', 'LZJ008A2', 'LZJ008A2'),
    ('ZJ', 'LC', 'LZJ010A2', 'LZJ010A2'),
    ('ZJ', 'LC', 'LZJ050A2S', 'LZJ050A2S（3瓦）'),
    ('ZJ', 'NV', 'JETSONORINNANO8GB', 'Jetson Orin Nano 8GB'),
    ('ZJ', 'NV', 'JETSONORINSERIES', 'JETSON ORIN SERIES'),
    ('ZJ', 'KC', 'JRC3000', 'JRC3000'),
]

# 市场侧缺型号、会阻塞自身 SC 编码生成，需仓管补录。
MARKET_NEEDS_MODEL = [
    {'product': 'HUAWEI Pad', 'manufacturer': '华为', 'model_raw': '',
     'reason': '型号为空，补齐后才能生成市场编码'},
]

# 研发侧型号缺失或为描述性文字。研发资产走 DZSB- 行政编码，不依赖型号，
# 故不阻塞导入，仅供仓管整理数据时参考。
RND_MODEL_NOTES = [
    {'product': '实验室X30盒子', 'manufacturer': '八维通', 'model_raw': '展老师手搓'},
    {'product': '海塔迷你主机', 'manufacturer': '', 'model_raw': '罗思玮归还'},
    {'product': '气体传感器', 'manufacturer': '承已', 'model_raw': '四合一'},
    {'product': '气体传感器', 'manufacturer': '承已', 'model_raw': '六合一'},
    {'product': 'LZJ010A2', 'manufacturer': '', 'model_raw': '厂家换新'},
    {'product': '大华云台', 'manufacturer': '大华', 'model_raw': '热成像三轴云台'},
    {'product': '灵巧手', 'manufacturer': '因时', 'model_raw': '右手'},
    {'product': '灵巧手', 'manufacturer': '因时', 'model_raw': '左手'},
    {'product': '消防水炮', 'manufacturer': '大连汇高', 'model_raw': '轻量化水炮'},
]


def seed(apps, schema_editor):
    AssetTypeCode = apps.get_model('inventory', 'AssetTypeCode')
    ProductModelCode = apps.get_model('inventory', 'ProductModelCode')
    Manufacturer = apps.get_model('inventory', 'Manufacturer')

    type_map = {}
    for code, name in DEVICE_TYPES:
        obj, _ = AssetTypeCode.objects.get_or_create(code=code, defaults={'name': name})
        type_map[code] = obj

    mfg_map = {}
    for code, name in MANUFACTURERS:
        if not code:
            continue
        obj, _ = Manufacturer.objects.get_or_create(code=code, defaults={'name': name})
        mfg_map[code] = obj

    for type_code, mfg_code, model_code, model_name in PRODUCT_MODELS:
        asset_type = type_map.get(type_code)
        if asset_type is None:
            continue
        ProductModelCode.objects.get_or_create(
            asset_type_code=asset_type,
            code=model_code,
            defaults={
                'manufacturer': mfg_map.get(mfg_code),
                'name': model_name,
            },
        )


def unseed(apps, schema_editor):
    # 正向迁移使用 get_or_create，无法可靠区分“迁移创建”和“用户此前创建”的
    # 同码记录。逆向删除有误删基础资料的风险，因此有意保持为空操作。
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0023_asset_identity_v2'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
