// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title ServiceRegistry
 * @notice Agent 服务注册表 —— 供应商在这里发布服务，消费者在这里查找服务
 *
 * 场景：金融数据 Agent 调用 registerService() 发布"财报查询"服务
 *       标价 0.01 ETH/次。股票分析 Agent 调用 getService() 找到它并调用。
 */
contract ServiceRegistry {

    // ── 数据结构 ──

    struct Service {
        uint256 id;             // 服务 ID
        address provider;       // 供应商地址（谁提供的）
        string name;            // 服务名称，如 "financial_report_query"
        string description;     // 服务描述，如 "查询美股上市公司的财报核心指标"
        uint256 price;          // 每次调用的价格（单位：wei）
        bool isActive;          // 是否还在提供
    }

    // 所有服务列表
    Service[] public services;

    // 根据服务名查找活跃服务的索引（用于快速查询）
    mapping(string => uint256[]) private nameToServiceIds;

    // 事件
    event ServiceRegistered(
        uint256 indexed serviceId,
        address indexed provider,
        string name,
        uint256 price
    );
    event ServiceUpdated(uint256 indexed serviceId, uint256 newPrice, bool isActive);

    // ── 供应商操作 ──

    /**
     * @notice 供应商注册一个新服务
     * @param _name 服务名称
     * @param _description 服务描述
     * @param _price 每次调用的价格（wei）
     */
    function registerService(
        string calldata _name,
        string calldata _description,
        uint256 _price
    ) external {
        require(_price > 0, "price must be > 0");

        uint256 serviceId = services.length;
        services.push(Service({
            id: serviceId,
            provider: msg.sender,
            name: _name,
            description: _description,
            price: _price,
            isActive: true
        }));

        nameToServiceIds[_name].push(serviceId);

        emit ServiceRegistered(serviceId, msg.sender, _name, _price);
    }

    /**
     * @notice 供应商更新已有的服务（价格、状态）
     */
    function updateService(
        uint256 _serviceId,
        uint256 _newPrice,
        bool _isActive
    ) external {
        require(_serviceId < services.length, "service does not exist");
        Service storage s = services[_serviceId];
        require(s.provider == msg.sender, "only provider can update");

        s.price = _newPrice;
        s.isActive = _isActive;

        emit ServiceUpdated(_serviceId, _newPrice, _isActive);
    }

    // ── 消费者查询 ──

    /**
     * @notice 根据名称查找所有活跃的服务
     * @return 服务列表 + 对应的供应商地址和价格
     */
    function getService(string calldata _name)
        external
        view
        returns (Service[] memory)
    {
        uint256[] memory ids = nameToServiceIds[_name];
        uint256 count = ids.length;

        // 先统计活跃数量
        uint256 activeCount = 0;
        for (uint256 i = 0; i < count; i++) {
            if (services[ids[i]].isActive) {
                activeCount++;
            }
        }

        // 只返回活跃服务
        Service[] memory result = new Service[](activeCount);
        uint256 index = 0;
        for (uint256 i = 0; i < count; i++) {
            if (services[ids[i]].isActive) {
                result[index] = services[ids[i]];
                index++;
            }
        }
        return result;
    }

    /**
     * @notice 获取某个供应商注册的所有服务
     */
    function getProviderServices(address _provider)
        external
        view
        returns (Service[] memory)
    {
        uint256 count = services.length;
        uint256 providerCount = 0;

        for (uint256 i = 0; i < count; i++) {
            if (services[i].provider == _provider) {
                providerCount++;
            }
        }

        Service[] memory result = new Service[](providerCount);
        uint256 index = 0;
        for (uint256 i = 0; i < count; i++) {
            if (services[i].provider == _provider) {
                result[index] = services[i];
                index++;
            }
        }
        return result;
    }

    function getServiceCount() external view returns (uint256) {
        return services.length;
    }
}
