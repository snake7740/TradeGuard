-- UnifiedModel 语义运行时退化路径（04 §2 替换成本列声明：SQL 视图兜底）
-- 将 03 §3 的 Node/Link 语义模型以视图实现，query_related_graph（API-M-02）直接查 v_graph_edge
-- 复赛接入 UnifiedModel 运行时后，仅需替换 mcp-core 的查询后端，视图保留作对账

-- 边表：主体间关系（SAME_PAYEE / SAME_DEVICE / SAME_IPSEG）
CREATE OR REPLACE VIEW v_graph_edge AS
SELECT DISTINCT
  t1.account_hash            AS src_node,
  t2.account_hash            AS dst_node,
  'SAME_PAYEE'::varchar(16)  AS edge_type,
  count(*)                   AS weight
FROM transaction t1
JOIN transaction t2
  ON t2.payee_hash = t1.payee_hash
 AND t2.account_hash <> t1.account_hash
 AND t1.payee_hash IS NOT NULL
GROUP BY t1.account_hash, t2.account_hash
UNION ALL
SELECT DISTINCT
  t1.account_hash, t2.account_hash, 'SAME_DEVICE', count(*)
FROM transaction t1
JOIN transaction t2
  ON t2.device_fp_hash = t1.device_fp_hash
 AND t2.account_hash <> t1.account_hash
 AND t1.device_fp_hash IS NOT NULL
GROUP BY t1.account_hash, t2.account_hash
UNION ALL
SELECT DISTINCT
  t1.account_hash, t2.account_hash, 'SAME_IPSEG', count(*)
FROM transaction t1
JOIN transaction t2
  ON host(network(set_masklen(t1.ip, 24))) = host(network(set_masklen(t2.ip, 24)))
 AND t2.account_hash <> t1.account_hash
 AND t1.ip IS NOT NULL
GROUP BY t1.account_hash, t2.account_hash;

GRANT SELECT ON v_graph_edge TO tg_web, tg_app;

-- 2 跳邻居查询函数（图扩展深度上限 2 跳，AA-SK-02 安全边界）
CREATE OR REPLACE FUNCTION fn_related_graph(p_account char(64), p_hops int DEFAULT 2)
RETURNS TABLE(src_node char(64), dst_node char(64), edge_type varchar(16), weight bigint, hop int)
LANGUAGE sql STABLE AS $$
  WITH RECURSIVE walk AS (
    SELECT e.src_node, e.dst_node, e.edge_type, e.weight, 1 AS hop
    FROM v_graph_edge e WHERE e.src_node = p_account
    UNION ALL
    SELECT e.src_node, e.dst_node, e.edge_type, e.weight, w.hop + 1
    FROM v_graph_edge e JOIN walk w ON e.src_node = w.dst_node
    WHERE w.hop < LEAST(p_hops, 2) AND e.dst_node <> p_account
  )
  SELECT * FROM walk;
$$;

GRANT EXECUTE ON FUNCTION fn_related_graph(char(64), int) TO tg_web, tg_app;
