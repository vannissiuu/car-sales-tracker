# 国产车销量数据源探测报告

生成时间 (UTC): 2026-08-26T10:58:42.045313+00:00

本报告由 `scripts/probe_sources.py` 在 GitHub Actions runner 上自动生成，
目的是回答两个问题：runner 能不能访问目标网站？如果能，网站返回的数据长什么样？

## Runner 出口信息

- IP: `20.81.47.118`
- 地区: Dulles Town Center, Virginia, US
- 组织/ISP: AS8075 Microsoft Corporation

（如果目标网站有地域限制，这个 IP/地区信息是判断是否被地域屏蔽的关键线索。）

## 阶段 A：URL 可达性

| URL | 状态码 | 耗时(秒) | 正文长度 | 错误 |
|---|---|---|---|---|
| https://www.dongchedi.com/ | 200 | 1.327 | 190189 |  |
| https://www.dongchedi.com/sales | 200 | 0.312 | 6253 |  |
| https://www.cpcaauto.com/ | 403 | 1.168 | 33 |  |
| http://www.caam.org.cn/tjsj | 200 | 2.049 | 96500 |  |

## 阶段 B：Playwright 渲染与接口抓包

### https://www.dongchedi.com/sales

- 是否尝试: True
- 捕获到的 XHR/fetch 响应总数: 19
- 其中 JSON 接口数量: 9
- 渲染后 HTML: `probe_output/B_01_www.dongchedi.com_sales_rendered.html`
- 截图: `probe_output/B_01_www.dongchedi.com_sales_screenshot.png`

**捕获到的 JSON 接口及其字段名（用于判断能否拿到六个维度）：**

- `B_xhr_01_01_www.dongchedi.com_sales.json`
  - 来源 URL: https://vcs.snssdk.com/vc/setting
  - 字段名: `action_timeout`, `agreement_title`, `agreement_url`, `agreement_version`, `alpha`, `async_collect_time_out`, `auth`, `back_up_host`, `back_up_js_v2`, `black_min_score`, `blur_check_enabled`, `boe`, `cancellable`, `cn`, `collect_page_history`, `collect_page_history_pre`, `collect_touch_event`, `collect_touch_event_pre`, `common`, `compute_delay_after_trigger_ms`, `enable`, `frequency`, `gray_h5_resources`, `gray_js`, `gray_js_v2`, `gray_label_report_sample_rate`, `gray_url`, `h5_load_retry_enable`, `h5_load_timeout`, `h5_resources`, `h5_verify_acc_switch`, `h5_verify_gyro_switch`, `height`, `help_url`, `host`, `identity_use_dialog_v2`, `in`, `js`, `js_v2`, `live_dispatch_enable`, `matched_path_buffer_size`, `md5`, `min_interval_ms`, `model`, `model_file_map`, `name`, `nocaptcha`, `nocaptcha_client_compute`, `nocaptcha_enable`, `path_cold_start_delay_ms`, `path_enable`, `path_list`, `period`, `popup_url`, `pre_create`, `qa`, `ratio`, `report_time_out`, `report_url`, `request_encrypt`, `retry_count`, `retry_interval`, `rgb`, `scene_level`, `self_unpunish`, `senseless_config.json`, `senseless_model.bytenn`, `sensor_max_num`, `sensor_update_interval`, `server_calibrated_timestamp`, `sg`, `skip_launch`, `smarter_verify`, `smartest_verify`, `sms`, `timeout`, `trigger`, `trigger_sec_sdk`, `url`, `url_list`, `use_bytenn`, `use_cache`, `use_dialog_size_v2`, `use_jsb_request`, `use_native_report`, `use_sec_camera`, `va`, `verify`, `verify_cancellable`, `verify_identity`, `verify_use_dialog_v2`, `version`, `white_label_report_sample_rate`, `white_max_score`, `width`
- `B_xhr_01_02_www.dongchedi.com_sales.json`
  - 来源 URL: https://mon.zijieapi.com/monitor_web/settings/browser-settings?bid=rmc_verifycenter&store=1
  - 字段名: `action`, `apdex`, `cls`, `data`, `duration_apdex`, `errmsg`, `errno`, `error_weight`, `fcp`, `fp`, `frustrating_threshold`, `heatmap`, `include_users`, `last_n`, `lcp`, `open_list`, `perf_apdex`, `plugins`, `quota_rate`, `refresh_after`, `rules`, `sample`, `sample_granularity`, `sample_rate`, `satisfying_threshold`, `spa_load`, `timestamp`, `updated_at`, `url`, `user_id`
- `B_xhr_01_03_www.dongchedi.com_sales.json`
  - 来源 URL: https://verify.zijieapi.com/captcha/i18n?aid=24140&lang=zh&bd_version=1.0.0.892&fp=verify_mt9zco2z_5FvVZQWq_ciK1_460N_9Zut_c4jU34eUfA2b&h5_check_version=3.5.2&os_name=windows&platform=pc&os_type=2&h5_sdk_version=3.5.80&webdriver=true&tmp=1787741873957
  - 字段名: `H5_VerifyTips_11`, `H5_VerifyTips_12`, `H5_VerifyTips_13`, `H5_VerifyTips_14`, `code`, `confirm`, `data`, `img_error`, `loading`, `message`, `net_error`, `refresh`, `slide_prompt`, `slide_prompt_whirl`, `slide_tip`, `text_title_1`, `text_title_whirl`
- `B_xhr_01_04_www.dongchedi.com_sales.json`
  - 来源 URL: https://mcs.zijieapi.com/webid
  - 字段名: `e`, `web_id`
- `B_xhr_01_05_www.dongchedi.com_sales.json`
  - 来源 URL: https://mcs.zijieapi.com/list
  - 字段名: `e`, `sc`, `tc`
- `B_xhr_01_06_www.dongchedi.com_sales.json`
  - 来源 URL: https://mcs.zijieapi.com/list
  - 字段名: `e`, `sc`
- `B_xhr_01_07_www.dongchedi.com_sales.json`
  - 来源 URL: https://mcs.zijieapi.com/list
  - 字段名: `e`, `sc`, `tc`
- `B_xhr_01_08_www.dongchedi.com_sales.json`
  - 来源 URL: https://mcs.zijieapi.com/list
  - 字段名: `e`, `sc`, `tc`
- `B_xhr_01_09_www.dongchedi.com_sales.json`
  - 来源 URL: https://mcs.zijieapi.com/list
  - 字段名: `e`, `sc`, `tc`

### https://www.dongchedi.com/

- 是否尝试: True
- 捕获到的 XHR/fetch 响应总数: 51
- 其中 JSON 接口数量: 30
- 渲染后 HTML: `probe_output/B_02_www.dongchedi.com_rendered.html`
- 截图: `probe_output/B_02_www.dongchedi.com_screenshot.png`

**捕获到的 JSON 接口及其字段名（用于判断能否拿到六个维度）：**

- `B_xhr_02_01_www.dongchedi.com.json`
  - 来源 URL: https://www.dongchedi.com/tt-anti-token
  - 字段名: `code`, `data`, `errno`, `message`
- `B_xhr_02_02_www.dongchedi.com.json`
  - 来源 URL: https://www.dongchedi.com/motor/car_service/open_api/get_ip_city/?aid=1839&app_name=auto_web_pc&a_bogus=Oj4jkH6wmZR5cplG8CGSHVBliXfMrF8jx-i2SCSktPOvywzbwLP5Crc4aoq9s-6hXbB5xFVHuf0AbDVcZ2Xs3lHkLmpDSNzRruxcVX8L0qw6YFJsEHjhCw0zuwsKWbTLl%2FctiI65AsMNZdcl9NAhAQ5GS5zqBObpbHZRd%2FYyejAUpz8zD1BbtaX2bH-tB-d6%2FGkyHGY%3D
  - 字段名: `message`, `prompts`, `status`
- `B_xhr_02_03_www.dongchedi.com.json`
  - 来源 URL: https://www.dongchedi.com/motor/pc/car/rank_data_config?aid=1839&app_name=auto_web_pc&a_bogus=QJ0VgwyEQ2mjapeG8cGSH3%2FlxhdlrMWjdMi2SCtk9PYDLqzbwjN9Cce4nowH44yhXuB5xFIHuf0AYEVcqIUw3ArpzmpvSkvWHuxAVWmohqwXbzvsDrjiCw8zLwsFW5GLe%2FcUiAfRXsMNZDOlIrATAQ-ay5Fo-mbpRqMbdMuyyjA8pFWzg1pjtnf2JH-tB-d6%2FG7yHYS%3D
  - 字段名: `brand_id`, `capacity_l`, `city`, `data`, `filters`, `icon`, `is_publish_time`, `key`, `manufacturer`, `message`, `min_version_code`, `month`, `new_energy_type`, `new_version`, `options`, `price`, `prompts`, `rank_data_type`, `score_type`, `selected_icon`, `sells_rank_month`, `status`, `sub_tab_list`, `tab_list`, `text`, `title`
- `B_xhr_02_04_www.dongchedi.com.json`
  - 来源 URL: https://www.dongchedi.com/motor/m_api/conf?aid=1839&app_name=auto_web_pc&conf_key=agreement_update_announcement_pc&a_bogus=YJ0jkqW7QN5nepeGmCJ6HVBlO7jMNMWjLFTObotkSNOBLqtbhENICce-coqH44Lh7bBIxF1HLf0%2FYDncpdXz3ArkumkvudXS3SxnVUfo%2FqNXYMJsLNjsCL8zuwsC8csLaAn7iAf5Is0r1EOlVNATApAGy5FqBYbpRHMSdMzy9jAW3M8zD3BWtr62JH-zB4xW8bA9tE%3D%3D
  - 字段名: `data`, `message`, `prompts`, `status`
- `B_xhr_02_05_www.dongchedi.com.json`
  - 来源 URL: https://mon.zijieapi.com/monitor_web/settings/browser-settings?bid=motor_pc&store=1
  - 字段名: `action`, `apdex`, `cls`, `data`, `duration_apdex`, `errmsg`, `errno`, `error_weight`, `fcp`, `fp`, `frustrating_threshold`, `heatmap`, `include_users`, `last_n`, `lcp`, `open_list`, `perf_apdex`, `plugins`, `quota_rate`, `refresh_after`, `rules`, `sample`, `sample_granularity`, `sample_rate`, `satisfying_threshold`, `timestamp`, `updated_at`, `url`, `user_id`
- `B_xhr_02_06_www.dongchedi.com.json`
  - 来源 URL: https://www.dongchedi.com/motor/car_service/open_api/get_ip_city/?aid=1839&app_name=auto_web_pc&a_bogus=O7sfhHyyDpmRP3AS8CJyH5%2Fl6HfMr0ujdMTOSebkHPOkLqtbkENACce-noqHs46hvYBVxKV7LfMlYdncZdUi3AnkKmkkS%2FXyHYOnVXsoMqqXYMisENj0Cu0zKwBC8bsLl%2FcUil65WsMNZEQlIHAiAQ5Gy5z9BYYpRNMydZzySjWW30SzLZB6tcX2JH-S-4x6MGGfHKL%3D
  - 字段名: `message`, `prompts`, `status`
- `B_xhr_02_07_www.dongchedi.com.json`
  - 来源 URL: https://www.dongchedi.com/motor/ad/m/pc/group?aid=1839&app_name=auto_web_pc&group_type=headline&a_bogus=Qy4fgF67EZmnP3eb8CGfH3%2FUK7xArF8jhliObeDkyNFWyqUTkLPICra4aoqos-ghvuB5xF37uf0AYxxcZdX03lHkwmpvSDhfHYOIVXsLMqw6bzvsgrbwCz8FowBCW5GLeQcvi1v5Ws0rZD5lIHI0Adla95z9-YmpSNZfd%2FT9yjW830Szg1pjtcfgGH-z-aV6OGkfHED%3D
  - 字段名: `data`, `message`, `prompts`, `status`
- `B_xhr_02_08_www.dongchedi.com.json`
  - 来源 URL: https://www.dongchedi.com/motor/ad/m/pc/group?aid=1839&app_name=auto_web_pc&group_type=capsule&a_bogus=mjsRkwUyOo8jC3CbucJSH5MU1ZflrMSjXBTQW7gptxPPywtGijNnCcC-Gow9s-LhXup9xoVHqfMlYxncpIXk3ArpqmkfS8vbHuxAVX0Lgqw6YzksEHbwCu8FzwBKWR4Le5c7ilXR6sMN1dQlINA0AB5G95F9-mRpWNZSdMuySjA030uzg1pjtn62aH-u-ad6BThRHiS%3D
  - 字段名: `data`, `message`, `prompts`, `status`
- `B_xhr_02_09_www.dongchedi.com.json`
  - 来源 URL: https://www.dongchedi.com/motor/searchpage/launcher/main/v1/?aid=1839&app_name=auto_web_pc&a_bogus=QvsRDHUyDdQbcpebmCJjH-QluZjlrM8jdBi2SHbpSPYfLhzbijNCCnC-noq9saLhvbpVxq37wfM%2FYddcNdXz3InkwmpDuDij3bOVVX6ogqigYFksEqbzCwsFowsCWcTLl%2FnXiIf5WsMNZD5lIHI0AQ5a95zHBQRpWHMRdZT9tjWW3MWzEZpftrf2jH4zBaIWB8XtCj%3D%3D
  - 字段名: `animate_time`, `bottom_content`, `bubble_info`, `children_rank_list`, `count`, `data`, `default`, `discovery_words`, `dynamic_code`, `feedback_button`, `have_children_rank`, `history_list`, `hot_search_roll_info_v2`, `hot_search_str`, `hot_search_words`, `insert_mode`, `interval_time`, `item_id`, `lua_search_hot`, `lua_v5`, `message`, `open_url`, `origin`, `prefetch`, `rank_board`, `rank_board_v2`, `rank_code`, `rank_name`, `recall_type`, `req_id`, `schema`, `supply_type`, `tag_border_color`, `tag_border_color_dark`, `tag_color`, `tag_color_dark`, `tag_style`, `text`, `timeout`, `tops`, `track`, `word_list`
- `B_xhr_02_10_www.dongchedi.com.json`
  - 来源 URL: https://www.dongchedi.com/passport/account/info/v2/?aid=1839&app_name=auto_web_pc
  - 字段名: `data`, `description`, `error_code`, `message`, `name`, `session_key`, `user_id`
- `B_xhr_02_11_www.dongchedi.com.json`
  - 来源 URL: https://www.dongchedi.com/ttwid/report_fingerprint/
  - 字段名: `message`, `status_code`
- `B_xhr_02_12_www.dongchedi.com.json`
  - 来源 URL: https://vcs.zijieapi.com/vc/setting
  - 字段名: `action_timeout`, `agreement_title`, `agreement_url`, `agreement_version`, `alpha`, `async_collect_time_out`, `auth`, `back_up_host`, `back_up_js_v2`, `black_min_score`, `blur_check_enabled`, `boe`, `cancellable`, `cn`, `collect_page_history`, `collect_page_history_pre`, `collect_touch_event`, `collect_touch_event_pre`, `common`, `compute_delay_after_trigger_ms`, `enable`, `frequency`, `gray_h5_resources`, `gray_js`, `gray_js_v2`, `gray_label_report_sample_rate`, `gray_url`, `h5_load_retry_enable`, `h5_load_timeout`, `h5_resources`, `h5_verify_acc_switch`, `h5_verify_gyro_switch`, `height`, `help_url`, `host`, `identity_use_dialog_v2`, `in`, `js`, `js_v2`, `live_dispatch_enable`, `matched_path_buffer_size`, `md5`, `min_interval_ms`, `model`, `model_file_map`, `name`, `nocaptcha`, `nocaptcha_client_compute`, `nocaptcha_enable`, `path_cold_start_delay_ms`, `path_enable`, `path_list`, `period`, `popup_url`, `pre_create`, `qa`, `ratio`, `report_time_out`, `report_url`, `request_encrypt`, `retry_count`, `retry_interval`, `rgb`, `scene_level`, `self_unpunish`, `senseless_config.json`, `senseless_model.bytenn`, `sensor_max_num`, `sensor_update_interval`, `server_calibrated_timestamp`, `sg`, `skip_launch`, `smarter_verify`, `smartest_verify`, `sms`, `timeout`, `trigger`, `trigger_sec_sdk`, `url`, `url_list`, `use_bytenn`, `use_cache`, `use_dialog_size_v2`, `use_jsb_request`, `use_native_report`, `use_sec_camera`, `va`, `verify`, `verify_cancellable`, `verify_identity`, `verify_use_dialog_v2`, `version`, `white_label_report_sample_rate`, `white_max_score`, `width`
- `B_xhr_02_13_www.dongchedi.com.json`
  - 来源 URL: https://www.dongchedi.com/motor/pc/car/rank_data_config?aid=1839&app_name=auto_web_pc&msToken=ArOkaizwDnwewgXfdYKQT0tNM30tbEpbDowMragXavZWeV8n-TsYGdI6bY78OEU9FaK4yk-PEXfySJBTt-xOpogUFl8zfcez9GsLixCVaI320IQiu8DUe-WaRb5oGYRX3o3mNRIarp4MZd0J6M8v2j6-ZUfvyRnt2w%3D%3D&a_bogus=dJ0nkz6Lmo%2FjCpetmCG6HVAlzXolNFujXBTKWrbk7PPgyhMTDENVCxC4Goqq44yhJbBCxK3HofMAbdncqdUT3AnpwmpDuEt6HSO9VXso%2FqqXbMisgHbhCw8FqwsKW5sLeAnXilfR6s0xZD5lIqA0Ap5GH5FHBOmpWNMbd%2FT9CjW0pzWzDppRta62GH4z-ad6%2F4T6HS8%3D
  - 字段名: `brand_id`, `capacity_l`, `city`, `data`, `filters`, `icon`, `is_publish_time`, `key`, `manufacturer`, `message`, `min_version_code`, `month`, `new_energy_type`, `new_version`, `options`, `price`, `prompts`, `rank_data_type`, `score_type`, `selected_icon`, `sells_rank_month`, `status`, `sub_tab_list`, `tab_list`, `text`, `title`
- `B_xhr_02_14_www.dongchedi.com.json`
  - 来源 URL: https://datasail-cn-beijing.dcarapi.com/webid
  - 字段名: `e`, `web_id`
- `B_xhr_02_15_www.dongchedi.com.json`
  - 来源 URL: https://restapi.amap.com/v3/geocode/regeo?aid=1839&app_name=auto_web_pc&key=88db9775ba89ac7e7afafbecd43b96e7&location=0%2C0
  - 字段名: `adcode`, `addressComponent`, `aois`, `city`, `citycode`, `country`, `direction`, `distance`, `district`, `formatted_address`, `info`, `infocode`, `location`, `number`, `pois`, `province`, `regeocode`, `roadinters`, `roads`, `status`, `street`, `streetNumber`, `towncode`, `township`
- `B_xhr_02_16_www.dongchedi.com.json`
  - 来源 URL: https://vcs.snssdk.com/vc/setting
  - 字段名: `action_timeout`, `agreement_title`, `agreement_url`, `agreement_version`, `alpha`, `async_collect_time_out`, `auth`, `back_up_host`, `back_up_js_v2`, `black_min_score`, `blur_check_enabled`, `boe`, `cancellable`, `cn`, `collect_page_history`, `collect_page_history_pre`, `collect_touch_event`, `collect_touch_event_pre`, `common`, `compute_delay_after_trigger_ms`, `enable`, `frequency`, `gray_h5_resources`, `gray_js`, `gray_js_v2`, `gray_label_report_sample_rate`, `gray_url`, `h5_load_retry_enable`, `h5_load_timeout`, `h5_resources`, `h5_verify_acc_switch`, `h5_verify_gyro_switch`, `height`, `help_url`, `host`, `identity_use_dialog_v2`, `in`, `js`, `js_v2`, `live_dispatch_enable`, `matched_path_buffer_size`, `md5`, `min_interval_ms`, `model`, `model_file_map`, `name`, `nocaptcha`, `nocaptcha_client_compute`, `nocaptcha_enable`, `path_cold_start_delay_ms`, `path_enable`, `path_list`, `period`, `popup_url`, `pre_create`, `qa`, `ratio`, `report_time_out`, `report_url`, `request_encrypt`, `retry_count`, `retry_interval`, `rgb`, `scene_level`, `self_unpunish`, `senseless_config.json`, `senseless_model.bytenn`, `sensor_max_num`, `sensor_update_interval`, `server_calibrated_timestamp`, `sg`, `skip_launch`, `smarter_verify`, `smartest_verify`, `sms`, `timeout`, `trigger`, `trigger_sec_sdk`, `url`, `url_list`, `use_bytenn`, `use_cache`, `use_dialog_size_v2`, `use_jsb_request`, `use_native_report`, `use_sec_camera`, `va`, `verify`, `verify_cancellable`, `verify_identity`, `verify_use_dialog_v2`, `version`, `white_label_report_sample_rate`, `white_max_score`, `width`
- `B_xhr_02_17_www.dongchedi.com.json`
  - 来源 URL: https://www.dongchedi.com/motor/car_service/open_api/get_city_name/?aid=1839&app_name=auto_web_pc&city_name=&msToken=ArOkaizwDnwewgXfdYKQT0tNM30tbEpbDowMragXavZWeV8n-TsYGdI6bY78OEU9FaK4yk-PEXfySJBTt-xOpogUFl8zfcez9GsLixCVaI320IQiu8DUe-WaRb5oGYRX3o3mNRIarp4MZd0J6M8v2j6-ZUfvyRnt2w%3D%3D&a_bogus=OysVgF7imdQ5cpAb8cGbH-5U2Hx%2FNF8jYBT2RbXk7xTXLq0TDENcCnc4Joq94aLhXSpcxqIHLfMMbEVcp2Uz3IHpompDSYwj1Yx5VUmo2qwXYMksgrjkCL8FowsC8b4LaQcJiA6RUs0n1dQlnNI0ABlGy5FoBOmpWqM6d%2FTyyjAUpz8zD1B6trXgbH4t-aA6QTGjHzD%3D
  - 字段名: `city_name`, `data`, `match`, `message`, `prompts`, `status`
- `B_xhr_02_18_www.dongchedi.com.json`
  - 来源 URL: https://www.dongchedi.com/motor/ad/m/pc/banners?city_name=%E5%8C%97%E4%BA%AC&msToken=ArOkaizwDnwewgXfdYKQT0tNM30tbEpbDowMragXavZWeV8n-TsYGdI6bY78OEU9FaK4yk-PEXfySJBTt-xOpogUFl8zfcez9GsLixCVaI320IQiu8DUe-WaRb5oGYRX3o3mNRIarp4MZd0J6M8v2j6-ZUfvyRnt2w%3D%3D&a_bogus=DJsfhe6wm2mnapltmCJWH-1lGqn%2Frz8jZlTxROGpHPYTLZ0GEDPVCac4coqos-6hJupnxq17LfMMYxdcpIUk3IrpumpvSwGj3YxIVWvLMqwhY0vsgqb0CLmzqwsKWcTLaQcvilX5IsMn1D5lnrAkAd-Ge5zqBmbpbrZ6dZz9SjA8p08zL3pStafgnH-S-aIfsnS%3D
  - 字段名: `data`, `message`, `prompts`, `status`
- `B_xhr_02_19_www.dongchedi.com.json`
  - 来源 URL: https://restapi.amap.com/v3/geocode/regeo?aid=1839&app_name=auto_web_pc&key=88db9775ba89ac7e7afafbecd43b96e7&location=0%2C0
  - 字段名: `adcode`, `addressComponent`, `aois`, `city`, `citycode`, `country`, `direction`, `distance`, `district`, `formatted_address`, `info`, `infocode`, `location`, `number`, `pois`, `province`, `regeocode`, `roadinters`, `roads`, `status`, `street`, `streetNumber`, `towncode`, `township`
- `B_xhr_02_20_www.dongchedi.com.json`
  - 来源 URL: https://www.dongchedi.com/motor/car_service/open_api/get_city_name/?aid=1839&app_name=auto_web_pc&city_name=&msToken=ArOkaizwDnwewgXfdYKQT0tNM30tbEpbDowMragXavZWeV8n-TsYGdI6bY78OEU9FaK4yk-PEXfySJBTt-xOpogUFl8zfcez9GsLixCVaI320IQiu8DUe-WaRb5oGYRX3o3mNRIarp4MZd0J6M8v2j6-ZUfvyRnt2w%3D%3D&a_bogus=d70Rkz6yEx%2FVcplSuOJyHVOli7oArzuj8BTxRjXp7xPgLwzTqLPVCNc4coqqs4ghXSB9xK17ufM%2FbdxcqIXw3ArkFmkfuiXfruOnVXsL%2FqqhbzvsLrjwCwmFwwBKWbGLe%2Fn7i1XRls0rZDQlVNAhAQAat5F9-ObpbqMfdMz9HjAU3M8zEpBytc6gcH4tB-V6Qzi6H6m%3D
  - 字段名: `city_name`, `data`, `match`, `message`, `prompts`, `status`
- `B_xhr_02_21_www.dongchedi.com.json`
  - 来源 URL: https://datasail-cn-beijing.dcarapi.com/list
  - 字段名: `e`, `sc`, `tc`
- `B_xhr_02_22_www.dongchedi.com.json`
  - 来源 URL: https://datasail-cn-beijing.dcarapi.com/list
  - 字段名: `e`, `sc`, `tc`
- `B_xhr_02_23_www.dongchedi.com.json`
  - 来源 URL: https://security.zijieapi.com/api/metrics/emit
  - 字段名: `message`
- `B_xhr_02_24_www.dongchedi.com.json`
  - 来源 URL: https://datasail-cn-beijing.dcarapi.com/list
  - 字段名: `e`, `sc`, `tc`
- `B_xhr_02_25_www.dongchedi.com.json`
  - 来源 URL: https://sso.toutiao.com/check_login/?service=https:%2F%2Fwww.dongchedi.com%2F&aid=1839&account_sdk_source=sso
  - 字段名: `description`, `error_code`, `has_login`
- `B_xhr_02_26_www.dongchedi.com.json`
  - 来源 URL: https://datasail-cn-beijing.dcarapi.com/list
  - 字段名: `e`, `sc`, `tc`
- `B_xhr_02_27_www.dongchedi.com.json`
  - 来源 URL: https://datasail-cn-beijing.dcarapi.com/list
  - 字段名: `e`, `sc`, `tc`
- `B_xhr_02_28_www.dongchedi.com.json`
  - 来源 URL: https://datasail-cn-beijing.dcarapi.com/list
  - 字段名: `e`, `sc`, `tc`
- `B_xhr_02_29_www.dongchedi.com.json`
  - 来源 URL: https://datasail-cn-beijing.dcarapi.com/list
  - 字段名: `e`, `sc`, `tc`
- `B_xhr_02_30_www.dongchedi.com.json`
  - 来源 URL: https://datasail-cn-beijing.dcarapi.com/list
  - 字段名: `e`, `sc`, `tc`

### https://www.cpcaauto.com/

- 是否尝试: True
- 捕获到的 XHR/fetch 响应总数: 0
- 其中 JSON 接口数量: 0
- 渲染后 HTML: `probe_output/B_03_www.cpcaauto.com_rendered.html`
- 截图: `probe_output/B_03_www.cpcaauto.com_screenshot.png`

（未捕获到任何 JSON 接口——要么页面确实没有走 XHR/fetch 接口，
要么被验证码/登录墙拦下了，请查看对应的截图和渲染后 HTML 核实。）

## 需要人工核实的六个维度

年月 / 厂商集团 / 品牌 / 车体类型 / 车型 / 能源类型 / 销量

请对照上面每个 JSON 接口的字段名列表，以及 `C_*_page_text.txt` / `C_*_tables.json`
里的可见文本和表格内容，人工判断这些维度是否齐全。

## 已知的不确定性 / 推测部分

- 本探针的抓取逻辑是在**完全无法访问目标网站**的环境下编写的，
  因此接口路径、字段名的猜测**都没有被验证过**，脚本只是把 Playwright
  实际观察到的一切原样记录下来，并没有预设任何接口一定存在。
- 如果某个站点有验证码、登录墙或地域屏蔽，脚本不会尝试绕过，只会如实记录截图和 HTML。
- `caam.org.cn` 用的是 `http://` 而非 `https://`（原始需求如此），如果该站点强制跳转到
  https 或反过来，请求可能会有重定向，已记录在 `A_connectivity.json` 的 response_headers 里。
