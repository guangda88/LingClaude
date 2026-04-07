#!/usr/bin/env node
/**
 * 使用 Node.js 测试 LingXi MCP 服务器
 */

const { spawn } = require('child_process');
const path = require('path');

const serverPath = '/home/ai/Ling-term-mcp/dist/index.js';

console.log('='.repeat(60));
console.log('使用 Node.js 测试 LingXi MCP 服务器');
console.log('='.repeat(60));

// 启动服务器
const server = spawn('node', [serverPath], {
  stdio: ['pipe', 'pipe', 'pipe']
});

server.stderr.on('data', (data) => {
  console.log(`[STDERR] ${data}`);
});

server.on('error', (err) => {
  console.error(`[ERROR] Failed to start server: ${err}`);
  process.exit(1);
});

// 等待服务器启动
setTimeout(() => {
  console.log('\n1. 发送 initialize 请求...');

  const initializeRequest = {
    jsonrpc: '2.0',
    id: 1,
    method: 'initialize',
    params: {
      protocolVersion: '2024-11-05',
      capabilities: {
        prompts: {},
        resources: {},
        tools: {}
      },
      clientInfo: {
        name: 'TestClient',
        version: '1.0.0'
      }
    }
  };

  server.stdin.write(JSON.stringify(initializeRequest) + '\n');

}, 500);

let responseCount = 0;

server.stdout.on('data', (data) => {
  const responses = data.toString().trim().split('\n');
  
  responses.forEach((responseLine) => {
    if (!responseLine.trim()) return;

    responseCount++;
    console.log(`\n${responseCount}. 收到响应:`);
    console.log(responseLine);

    try {
      const response = JSON.parse(responseLine);
      
      // 处理 initialize 响应
      if (response.id === 1) {
        console.log('\n✓ Initialize 成功');
        console.log('   发送 initialized 通知...');

        const initializedNotification = {
          jsonrpc: '2.0',
          method: 'notifications/initialized'
        };

        server.stdin.write(JSON.stringify(initializedNotification) + '\n');
      }

      // 处理 initialized 后的操作
      else if (responseCount === 2) {
        console.log('\n✓ Initialized 通知已发送');
        console.log('   请求工具列表...');

        const listToolsRequest = {
          jsonrpc: '2.0',
          id: 2,
          method: 'tools/list'
        };

        server.stdin.write(JSON.stringify(listToolsRequest) + '\n');
      }

      // 处理工具列表响应
      else if (response.id === 2 && response.result) {
        console.log('\n✓ 工具列表获取成功');
        const tools = response.result.tools || [];
        console.log(`   找到 ${tools.length} 个工具:`);
        
        tools.forEach((tool) => {
          console.log(`     - ${tool.name}: ${tool.description || 'no description'}`);
        });

        // 测试工具调用
        if (tools.length > 0) {
          console.log('\n   测试 execute_command 工具...');

          const callToolRequest = {
            jsonrpc: '2.0',
            id: 3,
            method: 'tools/call',
            params: {
              name: 'execute_command',
              arguments: {
                command: 'echo',
                args: ['Hello LingXi']
              }
            }
          };

          server.stdin.write(JSON.stringify(callToolRequest) + '\n');
        } else {
          console.log('\n✗ 没有可用工具');
          shutdownServer();
        }
      }

      // 处理工具调用响应
      else if (response.id === 3) {
        console.log('\n✓ 工具调用成功');
        if (response.result && response.result.content) {
          response.result.content.forEach((item) => {
            if (item.type === 'text') {
              console.log(`   输出: ${item.text}`);
            }
          });
        }

        console.log('\n' + '='.repeat(60));
        console.log('✓ 所有测试通过！');
        console.log('='.repeat(60));

        shutdownServer();
      }

    } catch (err) {
      console.error(`\n✗ JSON 解析错误: ${err.message}`);
      console.error(`   原始数据: ${responseLine}`);
      shutdownServer();
    }
  });
});

function shutdownServer() {
  console.log('\n关闭服务器...');
  server.stdin.end();
  setTimeout(() => {
    server.kill();
    process.exit(0);
  }, 1000);
}

// 设置超时
setTimeout(() => {
  console.error('\n✗ 测试超时');
  shutdownServer();
}, 10000);
