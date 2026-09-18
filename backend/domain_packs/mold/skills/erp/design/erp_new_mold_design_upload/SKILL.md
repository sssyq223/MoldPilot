当用户要在 ERP 中上传新模钢料或五金设计清单时，先确认当前会话中已附加一份 XLSX、XLS 或 CSV 文件，并使用 `erp_design_parse_new_mold_upload`。默认省略 `sheet_type` 或使用 `auto`，由 D 盘 ERP 复用其现有表头解析规则自动识别钢料或五金；只有用户明确指定类型时才传 `steel` 或 `hardware`。该工具只建立 ERP 上传会话，不代表图纸处理完成。

图纸匹配遵循 ERP 的正式目录规则：优先按完整模号和零件号精确读取 `module_entrust/drawing/2d/{完整模号}/{零件号}.dwg|.dxf`。本地正式图纸已经存在时直接复用，不要求重复拆图或重新处理；仅在该模具正式目录尚无拆图文件时，ERP 才可按自身规则从 CAD 原图执行首次拆图。不得用 `(2)` 等重名副本顶替正式图纸，也不得跨 P 段匹配。

用户仅要求“解析/上传当前钢料或五金附件”时，只调用一次 `erp_design_parse_new_mold_upload` 并返回 ERP 上传会话，不得改为查询设计订单、ERP BOM 或 BOM 报表，也不得把附件 `file_id` 当作 BOM 查询条件。清单弹窗会按该会话继续读取图纸处理状态和最终表格。只有用户另行明确要求检查状态、核对明细或继续导入时，才使用 `erp_design_get_drawing_status`、`erp_design_get_upload_result`、`erp_design_validate_rows` 或后续工具。

“查看上传订单”“查看订单明细”表示读取本次上传会话的完整明细，不是查询 ERP BOM。“核算价格”“价格核算”“重新核价”在钢料清单中表示先取得本会话当前明细，再用 `erp_design_reprice_rows` 复用 ERP 现有价格、热处理与加工费规则；五金清单不调用该钢料专用接口，直接读取 ERP 解析结果中的已审批价和核算价。钢料的热处理建议值为“普通 / 真空 / 深冷”，可保留用户给出的其他明确值；时效处理只接受“是 / 否”，默认“否”，且仅在 ERP 支持的有效 `45#` 方料上允许选择“是”。修改时效、料型、材质或尺寸后必须再次调用 `erp_design_reprice_rows`，由 ERP 重新计算时效价；不得在 Agent 中另写价目匹配公式。“图纸预览”“预览图纸”表示从当前明细取得 `drawing_resource_id` 和该行 `drawing_preview_url`，再用 `erp_design_download_file` 的 `drawing_preview` 工件读取 ERP 已匹配的图纸；不得要求用户重新上传或重新处理已存在的正式图纸。

“判断公差”“公差档位”“长宽厚允许范围”或“对角公差”仅适用于钢料清单。只调用一次 `erp_design_evaluate_tolerances`，该工具会自行读取上传会话的完整明细和 `techRequirements.tolerance_table`，不需要先调用 `erp_design_get_upload_result`。公差值以 ERP 当次返回的技术要求为准，不在 Agent 中维护固定公差表。

“自动修正参数”“按图纸修正”“修正数量”“修正长宽厚”表示复用上传结果行中的 `drawing_material_shape`、`drawing_dimension_value`和 `drawing_quantity`：料型或尺寸修正必须整套替换，并清除不属于新料型的长宽或直径字段。钢料修正后使用 `erp_design_reprice_rows` 重新核算；五金不调用仅支持钢料的重新核价接口，其数量金额继续按 ERP 解析返回的已审批价、附图核算价和采购数量计算。不在 Agent 中重写 ERP 的价目匹配或加工费规则。

如果 ERP 明确返回历史无图明细，先展示受影响数量和当前图纸状态。只有用户明确确认重新匹配后，才可使用 `erp_design_rematch_no_drawing`；随后继续查询图纸状态，不把重新匹配请求当成图纸已归档。

在创建 ERP 请购或审批数据前，先使用 `erp_design_get_approval_config` 展示 ERP 审批配置、解析结果和异常。只有用户明确确认导入后，才可使用 `erp_design_import_new_mold`，且必须传入 `confirm_import: true`、交期和已经核对的明细。检测到 `ERP_DUPLICATE_CONFIRMATION_REQUIRED` 或“重复上传”时，展示 ERP 返回的既有单号（若有），不得直接重试；只有用户再次明确确认重复导入后，才以完全相同的会话和明细传入 `allow_duplicate: true`。

不得把图纸处理排队、解析成功、明细校验通过或审批配置读取成功表述为 ERP 请购已经创建、审批已经通过或采购已经下单；这些分别以 ERP 的导入及后续业务回执为准。
