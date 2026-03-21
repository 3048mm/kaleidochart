import codecs

path = r'd:\My Documents\Programing\stocktool\frontend\src\pages\DashboardPage.tsx'

with codecs.open(path, 'r', 'utf-8') as f:
    content = f.read()

replacements = {
    '繝・・繧ｿ縺ｪ縺・/div>': 'データなし</div>',
    '21EMA荵夜屬': '21EMA乖離',
    'ｧｭ Indicators': '🧭 Indicators',
    '訣 Markets': '🌍 Markets',
    '召 Sectors & Themes': '🏢 Sectors & Themes',
    'View Full Chart 窃・/Link>': 'View Full Chart →</Link>',
    'ｧｭ Leading Indicators (蜈郁｡梧欠讓・': '🧭 Leading Indicators (先行指標)',
    '訣 Global & Broad Markets (蜷・ｸょｴ繧､繝ｳ繝・ャ繧ｯ繧ｹ)': '🌍 Global & Broad Markets (各市場インデックス)',
    '召 Sectors Overview': '🏢 Sectors Overview',
    '櫨 Top Themes (1M RS Rank)': '🔥 Top Themes (1M RS Rank)',
    '笆ｲ 陦ｨ遉ｺ繧呈ｸ帙ｉ縺・(-10)': '▲ 表示を減らす (-10)',
    '笆ｼ 縺輔ｉ縺ｫ陦ｨ遉ｺ (+10)': '▼ さらに表示 (+10)',
    '笶・ｸ・Weak Themes (1M RS Rank)': '❄️ Weak Themes (1M RS Rank)',
    '藤 Themes RRG Map (Latest)': '📡 Themes RRG Map (Latest)',
    '窶ｻ 陦ｨ遉ｺ荳ｭ縺ｮ蜈ｨ繝・・繝橸ｼ・op & Weak・峨ｒ譛€譁ｰ縺ｮ 21譌･ RS Ratio/Momentum 縺ｧ繝励Ο繝・ヨ縺励※縺・∪縺吶€・': '※ 表示中の全テーマ（Top & Weak）を最新の 21日 RS Ratio/Momentum でプロットしています。'
}

for k, v in replacements.items():
    content = content.replace(k, v)

with codecs.open(path, 'w', 'utf-8') as f:
    f.write(content)
print('Done replacing.')