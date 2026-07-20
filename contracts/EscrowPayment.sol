// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title EscrowPayment
 * @notice Agent 间托管支付合约 —— 解决"谁先付钱"的信任问题
 *
 * 流程：
 *   1. Consumer（买方）调用 requestService()，支付费用，资金锁在合约里
 *   2. 合约触发 ServiceRequested 事件 → Provider（卖方）监听到后执行服务
 *   3. Provider 调用 deliverResult()，提交数据哈希上链作为凭证
 *   4. Consumer 验证数据 → 调用 confirmDelivery() → 合约放款给 Provider
 *      Consumer 觉得数据有问题 → 调用 dispute() → 进入仲裁流程
 *      超时未操作 → Consumer 可调用 refund() 退款
 *
 * 安全设计：
 *   - CEI 模式（Check-Effects-Interactions）：先检查状态，再更新状态，最后转账
 *   - ReentrancyGuard：noReentrancy 修饰器防止所有状态变更函数重入
 *   - 超时退款机制：Provider 超时未交付 → Consumer 可取回资金
 */
contract EscrowPayment {
    // ── 状态常量 ──
    enum RequestState { Pending, Delivered, Confirmed, Disputed, Refunded }

    // ── 数据结构 ──
    struct PaymentRequest {
        uint256 id;              // 请求 ID
        address consumer;        // 买方（消费方 Agent）
        address provider;        // 卖方（供应方 Agent）
        uint256 amount;          // 支付金额
        bytes32 dataHash;        // Provider 交付的数据哈希（用于验证数据完整性）
        RequestState state;      // 当前状态
        uint256 deadline;        // 超时时间戳（Consumer 在此之前需确认，否则可退款）
        uint256 serviceId;       // 关联的 ServiceRegistry 服务 ID
    }

    // 所有支付请求
    PaymentRequest[] public requests;

    // 防止重入攻击的锁
    bool private locked;

    // 引用 ServiceRegistry 合约地址
    address public serviceRegistry;

    // 超时时间（默认 30 分钟）
    uint256 public constant DEFAULT_TIMEOUT = 30 minutes;

    // 事件
    event ServiceRequested(
        uint256 indexed requestId,
        address indexed consumer,
        address indexed provider,
        uint256 amount,
        uint256 serviceId,
        uint256 deadline
    );
    event ResultDelivered(uint256 indexed requestId, bytes32 dataHash);
    event DeliveryConfirmed(uint256 indexed requestId);
    event DisputeRaised(uint256 indexed requestId);
    event Refunded(uint256 indexed requestId);

    // ── 修饰器 ──
    modifier noReentrancy() {
        require(!locked, "reentrancy not allowed");
        locked = true;
        _;
        locked = false;
    }

    // ── 构造函数 ──
    constructor(address _serviceRegistry) {
        serviceRegistry = _serviceRegistry;
    }

    // ── 核心流程 ──

    /**
     * @notice Step 1: Consumer 请求服务并支付费用
     * @param _provider 供应商地址
     * @param _serviceId 服务 ID
     */
    function requestService(
        address _provider,
        uint256 _serviceId
    ) external payable noReentrancy {
        require(msg.value > 0, "amount must be > 0");
        require(_provider != address(0), "invalid provider address");
        require(_provider != msg.sender, "cannot pay self");

        uint256 requestId = requests.length;
        requests.push(PaymentRequest({
            id: requestId,
            consumer: msg.sender,
            provider: _provider,
            amount: msg.value,
            dataHash: bytes32(0),
            state: RequestState.Pending,
            deadline: block.timestamp + DEFAULT_TIMEOUT,
            serviceId: _serviceId
        }));

        emit ServiceRequested(
            requestId,
            msg.sender,
            _provider,
            msg.value,
            _serviceId,
            block.timestamp + DEFAULT_TIMEOUT
        );
    }

    /**
     * @notice Step 2: Provider 交付结果，提交数据哈希上链存证
     * @param _requestId 请求 ID
     * @param _dataHash 交付数据的哈希（keccak256 后）
     *
     * 注意：这里只存哈希不上传原始数据（链上存储太贵）
     *       原始数据通过链下通道（HTTP/MCP）传递
     *       dataHash 用于 Consumer 验证数据是否被篡改
     */
    function deliverResult(uint256 _requestId, bytes32 _dataHash)
        external
        noReentrancy
    {
        PaymentRequest storage req = requests[_requestId];

        require(msg.sender == req.provider, "only provider can deliver");
        require(req.state == RequestState.Pending, "invalid state for delivery");
        require(_dataHash != bytes32(0), "data hash cannot be empty");

        req.state = RequestState.Delivered;
        req.dataHash = _dataHash;

        emit ResultDelivered(_requestId, _dataHash);
    }

    /**
     * @notice Step 3: Consumer 确认数据无误，合约直接放款给 Provider
     * @param _requestId 请求 ID
     *
     * 安全：遵循 CEI（Check-Effects-Interactions）模式
     *       先更新状态为 Confirmed，再执行转账
     *       noReentrancy 修饰器防止重入攻击
     */
    function confirmDelivery(uint256 _requestId) external noReentrancy {
        PaymentRequest storage req = requests[_requestId];

        require(msg.sender == req.consumer, "only consumer can confirm");
        require(req.state == RequestState.Delivered, "provider has not delivered");

        req.state = RequestState.Confirmed;

        // 放款给 Provider
        (bool sent, ) = payable(req.provider).call{value: req.amount}("");
        require(sent, "payment failed");

        emit DeliveryConfirmed(_requestId);
    }

    /**
     * @notice Consumer 对数据质量不满，发起争议
     * @param _requestId 请求 ID
     *
     * 当前版本：争议后资金暂时冻结，需要人工仲裁
     * 可扩展：可以接入 DAO 投票或预设仲裁者
     */
    function dispute(uint256 _requestId) external noReentrancy {
        PaymentRequest storage req = requests[_requestId];

        require(msg.sender == req.consumer, "only consumer can dispute");
        require(
            req.state == RequestState.Delivered,
            "only delivered state can dispute"
        );

        req.state = RequestState.Disputed;

        emit DisputeRaised(_requestId);
    }

    /**
     * @notice 超时退款 —— Provider 超时未交付，Consumer 拿回资金
     * @param _requestId 请求 ID
     */
    function refund(uint256 _requestId) external noReentrancy {
        PaymentRequest storage req = requests[_requestId];

        require(msg.sender == req.consumer, "only consumer can refund");
        require(
            req.state == RequestState.Pending,
            "only pending state can refund"
        );
        require(block.timestamp > req.deadline, "not yet expired");

        req.state = RequestState.Refunded;

        (bool sent, ) = payable(req.consumer).call{value: req.amount}("");
        require(sent, "refund failed");

        emit Refunded(_requestId);
    }

    // ── 查询 ──

    function getRequestCount() external view returns (uint256) {
        return requests.length;
    }

    function getRequest(uint256 _requestId)
        external
        view
        returns (PaymentRequest memory)
    {
        require(_requestId < requests.length, "request does not exist");
        return requests[_requestId];
    }

    /**
     * @notice 查询某个 Consumer 的所有请求
     */
    function getConsumerRequests(address _consumer)
        external
        view
        returns (PaymentRequest[] memory)
    {
        uint256 count = requests.length;
        uint256 consumerCount = 0;

        for (uint256 i = 0; i < count; i++) {
            if (requests[i].consumer == _consumer) {
                consumerCount++;
            }
        }

        PaymentRequest[] memory result = new PaymentRequest[](consumerCount);
        uint256 index = 0;
        for (uint256 i = 0; i < count; i++) {
            if (requests[i].consumer == _consumer) {
                result[index] = requests[i];
                index++;
            }
        }
        return result;
    }
}
