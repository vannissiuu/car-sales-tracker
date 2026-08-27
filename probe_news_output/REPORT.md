# P3 新闻源探针报告

生成时间（UTC）：2026-08-27T02:32:54.807208+00:00

本报告由 GitHub Actions runner（美国弗吉尼亚 Azure 出口 IP）实测生成。容器本地（本次调研所在的沙箱环境）无法访问中国网站，所以这份报告是唯一的真实连通性依据，调研阶段的判断仅供参考。

## 汇总表

| id | 来源 | 品牌 | 可靠度(调研阶段) | HTTP状态 | 疑似拦截 | 是否RSS | 提取条数 | 标题/链接/日期齐全 |
|---|---|---|---|---|---|---|---|---|
| 16888_news_home | 车主之家资讯 news.16888.com（首页/列表结构侦察） | - | search_confirmed | 200 (1120ms) | 否 | 否 | 20 (bs4_heuristic) | 20/20/19 (共20条) |
| 16888_news_category_qcwh | 车主之家资讯 news.16888.com 分类列表页样例（汽车文化分类） | - | search_confirmed | 200 (1321ms) | 否 | 否 | 20 (bs4_heuristic) | 20/20/2 (共20条) |
| sina_tagsnews_byd | 新浪汽车旧版标签聚合 tags.news.sina.com.cn（中文原词） | 比亚迪 | search_confirmed | 200 (1626ms) | 否 | 否 | 20 (bs4_heuristic) | 20/20/0 (共20条) |
| sina_tags_brand_wenjie | 新浪汽车新版标签聚合 tags.sina.com.cn/brand_<拼音> | 问界 | search_confirmed | 200 (2284ms) | 否 | 否 | 20 (bs4_heuristic) | 20/20/0 (共20条) |
| sina_tags_model_xiaomiqiche | 新浪汽车新版标签聚合 tags.sina.com.cn/model_<拼音> | 小米汽车 | search_confirmed | 200 (1233ms) | 否 | 否 | 20 (bs4_heuristic) | 20/20/1 (共20条) |
| sina_tags_brand_biyadi_guess | 新浪汽车新版标签聚合 tags.sina.com.cn/brand_biyadi（推测） | 比亚迪 | inferred_pattern | 200 (1197ms) | 否 | 否 | 20 (bs4_heuristic) | 20/20/0 (共20条) |
| mydrivers_tag_biyadi | 快科技（mydrivers）news.mydrivers.com/tag/<拼音>.htm | 比亚迪 | search_confirmed | 200 (1093ms) | 否 | 否 | 12 (bs4_heuristic) | 12/12/0 (共12条) |
| mydrivers_tag_wenjie_guess | 快科技 news.mydrivers.com/tag/wenjie.htm（推测） | 问界 | inferred_pattern | 200 (482ms) | 否 | 否 | 4 (bs4_heuristic) | 4/4/0 (共4条) |
| d1ev_search_byd | 第一电动 d1ev.com 站内搜索 /search?q=<关键词> | 比亚迪 | search_confirmed | 502 (4155ms) | 否 | 否 | 0 (none) | 0/0/0 (共0条) |

## 逐个详情

### 16888_news_home — 车主之家资讯 news.16888.com（首页/列表结构侦察）

- URL: https://news.16888.com/
- 测试品牌: （不针对具体品牌，用于结构侦察）
- 调研阶段可靠度: search_confirmed
- 调研备注: 与已验证可用的 xl.16888.com 同域名家族（16888.com）。搜索确认该子域存在且有新闻列表，但没搜到明确的『按品牌筛选』入口，本条只是用来侦察页面结构（是否有 tag/搜索/品牌筛选功能可用），不代表能按品牌构造 URL。
- HTTP 状态码: 200，耗时 1120ms
- 最终 URL: https://news.16888.com/（是否发生重定向: 否）
- Content-Type: text/html; charset=utf-8
- 编码: 声明=utf-8, 实测=utf-8
- 响应字节数: 49423
- 原始内容已保存: raw/16888_news_home.html
- 疑似被拦截(验证码/访问异常等关键词): 否
- RSS/Atom 解析: 否 — feedparser 解析出 0 条 entries（大概率不是 RSS/Atom）
- pandas.read_html: pandas.read_html 未找到表格或解析失败: `Import html5lib` failed.  Use pip or conda to install the html5lib package.
- BeautifulSoup 启发式列表: 提取到 20 条

  示例前 5 条：
  - 标题: 343个城市>>
    链接: https://www.16888.com/AreaList/
    日期: None
  - 标题: 长城H9力魂版正式上市 限时优惠焕新价21.49-23.29万
    链接: https://news.16888.com/a/2026/0826/24882642.html
    日期: 2026-08-26
  - 标题: 全新宝马i3后排堪比5系？操控好设计强就看价格了！
    链接: https://news.16888.com/a/2026/0826/24882379.html
    日期: 2026-08-26
  - 标题: 一汽丰田2027款bZ5正式上市 售价12.98-19.98万
    链接: https://news.16888.com/a/2026/0826/24882465.html
    日期: 2026-08-26
  - 标题: 广深两地2026年第8期拍牌价出炉 深圳铁牌均价下调
    链接: https://news.16888.com/a/2026/0825/24878610.html
    日期: 2026-08-25

### 16888_news_category_qcwh — 车主之家资讯 news.16888.com 分类列表页样例（汽车文化分类）

- URL: https://news.16888.com/qcwh/index_list_9.html
- 测试品牌: （不针对具体品牌，用于结构侦察）
- 调研阶段可靠度: search_confirmed
- 调研备注: 搜索直接命中的分类列表页，URL 形如 /<分类拼音缩写>/index_list_<N>.html，用于确认列表页 HTML 结构、翻页方式，为后续人工找品牌入口探路。
- HTTP 状态码: 200，耗时 1321ms
- 最终 URL: https://news.16888.com/qcwh/index_list_9.html（是否发生重定向: 否）
- Content-Type: text/html; charset=utf-8
- 编码: 声明=utf-8, 实测=utf-8
- 响应字节数: 26823
- 原始内容已保存: raw/16888_news_category_qcwh.html
- 疑似被拦截(验证码/访问异常等关键词): 否
- RSS/Atom 解析: 否 — feedparser 解析出 0 条 entries（大概率不是 RSS/Atom）
- pandas.read_html: pandas.read_html 未找到表格或解析失败: `Import html5lib` failed.  Use pip or conda to install the html5lib package.
- BeautifulSoup 启发式列表: 提取到 20 条

  示例前 5 条：
  - 标题: 343个城市>>
    链接: https://www.16888.com/AreaList/
    日期: None
  - 标题: 油价调整最新消息：汽柴油每升下调0.4/0.42元
    链接: https://news.16888.com/a/2026/0618/24693834.html
    日期: None
  - 标题: 玛莎拉蒂新款格雷嘉正式上市 售价57.88万起
    链接: https://news.16888.com/a/2026/0805/24823971.html
    日期: None
  - 标题: 油价调整最新消息：汽柴油每升下调0.41/0.43元
    链接: https://news.16888.com/a/2026/0604/24657826.html
    日期: None
  - 标题: 2026年5月汽车销量排行榜 零跑A10成新黑马
    链接: https://news.16888.com/a/2026/0617/24687455.html
    日期: None

### sina_tagsnews_byd — 新浪汽车旧版标签聚合 tags.news.sina.com.cn（中文原词）

- URL: https://tags.news.sina.com.cn/%E6%AF%94%E4%BA%9A%E8%BF%AA
- 测试品牌: 比亚迪
- 调研阶段可靠度: search_confirmed
- 调研备注: 搜索直接命中：『比亚迪 - 最新比亚迪实时滚动快讯...』标题的标签聚合页，URL 是中文品牌名直接 percent-encode，理论上可以对任意品牌名直接构造。
- HTTP 状态码: 200，耗时 1626ms
- 最终 URL: https://tags.news.sina.com.cn/%E6%AF%94%E4%BA%9A%E8%BF%AA（是否发生重定向: 否）
- Content-Type: text/html;charset=utf-8
- 编码: 声明=utf-8, 实测=utf-8
- 响应字节数: 187461
- 原始内容已保存: raw/sina_tagsnews_byd.html
- 疑似被拦截(验证码/访问异常等关键词): 否
- RSS/Atom 解析: 否 — feedparser 解析出 0 条 entries（大概率不是 RSS/Atom）
- pandas.read_html: pandas.read_html 未找到表格或解析失败: `Import html5lib` failed.  Use pip or conda to install the html5lib package.
- BeautifulSoup 启发式列表: 提取到 20 条

  示例前 5 条：
  - 标题: 陕西商州举行2026年保障比亚迪用工集中欢送仪式
    链接: https://finance.sina.com.cn/roll/2026-08-27/doc-inipthnz9095137.shtml
    日期: None
  - 标题: 工信部公示：比亚迪腾势Z9S申报电耗百公里12.6度
    链接: https://finance.sina.com.cn/stock/aigc/bwdt/gxbqcnyxh/2026-08-27/doc-inipthpc5862597.shtml
    日期: None
  - 标题: 10万级B级纯电轿车怎么选？秦MAX EV通勤出游实测+FAQ
    链接: https://k.sina.com.cn/article_7879777177_1d5abdb990680170oc.html
    日期: None
  - 标题: 20-25万配置高的车，智驾哪家强？
    链接: https://k.sina.com.cn/article_7879776391_1d5abd88706801iu5a.html
    日期: None
  - 标题: 20-25万预算，理想L6和比亚迪海豹08怎么选？
    链接: https://k.sina.com.cn/article_7879776391_1d5abd88706801iu58.html
    日期: None

### sina_tags_brand_wenjie — 新浪汽车新版标签聚合 tags.sina.com.cn/brand_<拼音>

- URL: https://tags.sina.com.cn/brand_wenjie
- 测试品牌: 问界
- 调研阶段可靠度: search_confirmed
- 调研备注: 搜索直接命中：『问界 - 最新问界实时滚动快讯...』，URL 是纯 ASCII 拼音，比中文 URL 更适合批量构造（不用处理编码）。同一套系统下还搜到 tags.sina.com.cn/model_qinplus（车型级）。
- HTTP 状态码: 200，耗时 2284ms
- 最终 URL: https://tags.sina.com.cn/brand_wenjie（是否发生重定向: 否）
- Content-Type: text/html;charset=utf-8
- 编码: 声明=utf-8, 实测=utf-8
- 响应字节数: 193987
- 原始内容已保存: raw/sina_tags_brand_wenjie.html
- 疑似被拦截(验证码/访问异常等关键词): 否
- RSS/Atom 解析: 否 — feedparser 解析出 0 条 entries（大概率不是 RSS/Atom）
- pandas.read_html: pandas.read_html 未找到表格或解析失败: `Import html5lib` failed.  Use pip or conda to install the html5lib package.
- BeautifulSoup 启发式列表: 提取到 20 条

  示例前 5 条：
  - 标题: 通勤增程车，雷克萨斯传统豪华 vs 新势力谁靠谱？3个维度说清+FAQ
    链接: https://k.sina.com.cn/article_7879849086_1d5acf47e06801h45y.html
    日期: None
  - 标题: 自动紧急制动哪些汽车有？实测这3个品牌最靠谱+FAQ
    链接: https://k.sina.com.cn/article_7879777164_1d5abdb8c06802zz1g.html
    日期: None
  - 标题: 自动避让系统车型推荐：理想/蔚来/问界谁更靠谱？+FAQ
    链接: https://k.sina.com.cn/article_7879776495_1d5abd8ef06801c6u6.html
    日期: None
  - 标题: 2026年哪些电车保值率最高？3个维度揭晓答案+FAQ
    链接: https://k.sina.com.cn/article_7879996096_1d5af32c006801kxga.html
    日期: None
  - 标题: 10万预算想买问界全时四驱？真相是M5和M7怎么选+FAQ
    链接: https://k.sina.com.cn/article_7879923805_1d5ae185d06801p8q8.html
    日期: None

### sina_tags_model_xiaomiqiche — 新浪汽车新版标签聚合 tags.sina.com.cn/model_<拼音>

- URL: https://tags.sina.com.cn/model_xiaomiqiche
- 测试品牌: 小米汽车
- 调研阶段可靠度: search_confirmed
- 调研备注: 搜索直接命中：『小米汽车 - 最新小米汽车实时滚动快讯...』。注意小米汽车用的是 model_ 前缀而不是 brand_ 前缀，说明 brand_/model_ 的划分不完全等于我们业务上的『品牌』概念，需要探针实测两种前缀。
- HTTP 状态码: 200，耗时 1233ms
- 最终 URL: https://tags.sina.com.cn/model_xiaomiqiche（是否发生重定向: 否）
- Content-Type: text/html;charset=utf-8
- 编码: 声明=utf-8, 实测=utf-8
- 响应字节数: 154985
- 原始内容已保存: raw/sina_tags_model_xiaomiqiche.html
- 疑似被拦截(验证码/访问异常等关键词): 否
- RSS/Atom 解析: 否 — feedparser 解析出 0 条 entries（大概率不是 RSS/Atom）
- pandas.read_html: pandas.read_html 未找到表格或解析失败: `Import html5lib` failed.  Use pip or conda to install the html5lib package.
- BeautifulSoup 启发式列表: 提取到 20 条

  示例前 5 条：
  - 标题: 2026成都车展哪个品牌最火？3个维度看透流量密码+FAQ
    链接: https://k.sina.com.cn/article_7880068362_1d5b04d0a06801ir9m.html
    日期: None
  - 标题: 计划明年要出海了！小米汽车海外账号已上线 社媒和官网都已开启
    链接: https://finance.sina.com.cn/tech/roll/2026-08-26/doc-iniprytk0873534.shtml
    日期: None
  - 标题: 小米汽车与宜家跨界联动，展示智能可变大空间SUV
    链接: https://k.sina.com.cn/article_7096020433_1a6f4add106801mve4.html
    日期: None
  - 标题: 小米汽车海外官网及社媒账号上线 官宣2027年进入欧洲市场
    链接: https://k.sina.com.cn/article_1644983660_620c756c02001uube.html?from=tech
    日期: None
  - 标题: 小米汽车海外官网正式上线
    链接: https://finance.sina.com.cn/roll/2026-08-26/doc-iniprytq2324674.shtml
    日期: None

### sina_tags_brand_biyadi_guess — 新浪汽车新版标签聚合 tags.sina.com.cn/brand_biyadi（推测）

- URL: https://tags.sina.com.cn/brand_biyadi
- 测试品牌: 比亚迪
- 调研阶段可靠度: inferred_pattern
- 调研备注: 按 brand_wenjie 的命名规律推测比亚迪应该是 brand_biyadi，没有被搜索直接命中，可能 404 或需要别的 slug（比如带后缀），探针就是要验证。
- HTTP 状态码: 200，耗时 1197ms
- 最终 URL: https://tags.sina.com.cn/guide（是否发生重定向: 是）
- Content-Type: text/html; charset=utf-8
- 编码: 声明=utf-8, 实测=utf-8
- 响应字节数: 304659
- 原始内容已保存: raw/sina_tags_brand_biyadi_guess.html
- 疑似被拦截(验证码/访问异常等关键词): 否
- RSS/Atom 解析: 否 — feedparser 解析出 0 条 entries（大概率不是 RSS/Atom）
- pandas.read_html: pandas.read_html 未找到表格或解析失败: `Import html5lib` failed.  Use pip or conda to install the html5lib package.
- BeautifulSoup 启发式列表: 提取到 20 条

  示例前 5 条：
  - 标题: Angelababy
    链接: https://tags.sina.com.cn/star_angelababy
    日期: None
  - 标题: 这个杀手不太冷静
    链接: https://tags.sina.com.cn/film_zhegeshashoubutailengjing
    日期: None
  - 标题: 我是真的讨厌异地恋
    链接: https://tags.sina.com.cn/film_woshizhendetaoyanyidilian
    日期: None
  - 标题: 我们的样子像极了爱情
    链接: https://tags.sina.com.cn/film_womendeyangzixiangjileaiqing
    日期: None
  - 标题: 世界上最爱我的人
    链接: https://tags.sina.com.cn/film_shijieshangaizuowoderen
    日期: None

### mydrivers_tag_biyadi — 快科技（mydrivers）news.mydrivers.com/tag/<拼音>.htm

- URL: https://news.mydrivers.com/tag/biyadi.htm
- 测试品牌: 比亚迪
- 调研阶段可靠度: search_confirmed
- 调研备注: 搜索直接命中『比亚迪最新资讯-快科技』及子车型页 biyadisong.htm（比亚迪宋），说明这套 tag 系统精细到车型级，URL 全 ASCII 拼音。快科技是综合科技媒体不是汽车垂直站，但确实有独立汽车频道。
- HTTP 状态码: 200，耗时 1093ms
- 最终 URL: https://news.mydrivers.com/tag/biyadi.htm（是否发生重定向: 否）
- Content-Type: text/html
- 编码: 声明=ISO-8859-1, 实测=utf-8
- 响应字节数: 42043
- 原始内容已保存: raw/mydrivers_tag_biyadi.html
- 疑似被拦截(验证码/访问异常等关键词): 否
- RSS/Atom 解析: 否 — feedparser 解析出 0 条 entries（大概率不是 RSS/Atom）
- pandas.read_html: pandas.read_html 未找到表格或解析失败: `Import html5lib` failed.  Use pip or conda to install the html5lib package.
- BeautifulSoup 启发式列表: 提取到 12 条

  示例前 5 条：
  - 标题: 比亚迪何志奇：再过几年轴距低于2800mm的车都会消失
    链接: //news.mydrivers.com/1/1146/1146572.htm
    日期: None
  - 标题: 比亚迪智能轮胎专利曝光：四轮可据路况自动充放气互不干扰
    链接: //news.mydrivers.com/1/1146/1146532.htm
    日期: None
  - 标题: 历时33天！比亚迪丝路万里行活动收官 车队成功抵达新加坡
    链接: //news.mydrivers.com/1/1146/1146522.htm
    日期: None
  - 标题: 迎战比亚迪海獭！日本车铃木e SKY亮相：电池用的是比亚迪
    链接: //news.mydrivers.com/1/1146/1146238.htm
    日期: None
  - 标题: 日本最大进口车经销商谈为什么卖比亚迪：他们全球销量第一
    链接: //news.mydrivers.com/1/1146/1146096.htm
    日期: None

### mydrivers_tag_wenjie_guess — 快科技 news.mydrivers.com/tag/wenjie.htm（推测）

- URL: https://news.mydrivers.com/tag/wenjie.htm
- 测试品牌: 问界
- 调研阶段可靠度: inferred_pattern
- 调研备注: 按 biyadi.htm 的命名规律推测问界是 wenjie.htm，没有被搜索命中过，问界是 2022 年才出现的新品牌，快科技这套 tag 系统是否覆盖到未知，探针验证。
- HTTP 状态码: 200，耗时 482ms
- 最终 URL: https://news.mydrivers.com/tag/wenjie.htm（是否发生重定向: 否）
- Content-Type: text/html
- 编码: 声明=ISO-8859-1, 实测=utf-8
- 响应字节数: 21000
- 原始内容已保存: raw/mydrivers_tag_wenjie_guess.html
- 疑似被拦截(验证码/访问异常等关键词): 否
- RSS/Atom 解析: 否 — feedparser 解析出 0 条 entries（大概率不是 RSS/Atom）
- pandas.read_html: pandas.read_html 未找到表格或解析失败: `Import html5lib` failed.  Use pip or conda to install the html5lib package.
- BeautifulSoup 启发式列表: 提取到 4 条

  示例前 5 条：
  - 标题: 特斯拉坚持纯视觉引争议！华为：我们用激光雷达 这是好处
    链接: //news.mydrivers.com/1/975/975548.htm
    日期: None
  - 标题: 问界接连卖爆！余承东力挺增程式：曾喊话快淘汰纯燃油车
    链接: //news.mydrivers.com/1/863/863530.htm
    日期: None
  - 标题: 豫ICP备2023031922号-1
    链接: https://beian.miit.gov.cn/
    日期: None
  - 标题: 豫公网安备 41010502003949号
    链接: http://www.beian.gov.cn/portal/registerSystemInfo?recordcode=41010502003949
    日期: None

### d1ev_search_byd — 第一电动 d1ev.com 站内搜索 /search?q=<关键词>

- URL: https://d1ev.com/search?q=%E6%AF%94%E4%BA%9A%E8%BF%AA
- 测试品牌: 比亚迪
- 调研阶段可靠度: search_confirmed
- 调研备注: 搜索命中了同结构的 /search?q=蔚来、/search?q=汽车，说明 q= 参数可以放任意关键词（含品牌名），是新能源垂直媒体，对比亚迪/问界/小米汽车这类新能源相关品牌覆盖度应该不错。
- HTTP 状态码: 502，耗时 4155ms
- 最终 URL: https://d1ev.com/search?q=%E6%AF%94%E4%BA%9A%E8%BF%AA（是否发生重定向: 否）
- Content-Type: text/html; charset=utf-8
- 编码: 声明=utf-8, 实测=ascii
- 响应字节数: 619
- 原始内容已保存: raw/d1ev_search_byd.html
- 疑似被拦截(验证码/访问异常等关键词): 否
- RSS/Atom 解析: 否 — feedparser 解析出 0 条 entries（大概率不是 RSS/Atom）
- pandas.read_html: 跳过（状态码非200或疑似被拦截）
- BeautifulSoup 启发式列表: 提取到 0 条

## 结论草稿（人工需要复核，这里只是把数据摆出来）

- 能访问(HTTP 200 且未疑似拦截)的源: 16888_news_home, 16888_news_category_qcwh, sina_tagsnews_byd, sina_tags_brand_wenjie, sina_tags_model_xiaomiqiche, sina_tags_brand_biyadi_guess, mydrivers_tag_biyadi, mydrivers_tag_wenjie_guess
- 确认是 RSS/Atom 的源: 
- 提取到至少 1 条新闻的源: 16888_news_home, 16888_news_category_qcwh, sina_tagsnews_byd, sina_tags_brand_wenjie, sina_tags_model_xiaomiqiche, sina_tags_brand_biyadi_guess, mydrivers_tag_biyadi, mydrivers_tag_wenjie_guess
