class ChatInterface {
    constructor() {
        this.messages = [];
        this.isLoading = false;
        this.init();
    }

    init() {
        this.createAuroraBackground();
        this.createChatInterface();
        this.bindEvents();
    }

    createAuroraBackground() {
        const body = document.body;
        const auroraContainer = document.createElement('div');
        auroraContainer.className = 'aurora-container';
        
        // Create multiple aurora layers
        for (let i = 0; i < 3; i++) {
            const aurora = document.createElement('div');
            aurora.className = 'aurora';
            auroraContainer.appendChild(aurora);
        }
        
        body.insertBefore(auroraContainer, body.firstChild);
    }    createChatInterface() {
        const app = document.getElementById('app');
        app.innerHTML = `
            <div class="side-panel">
                <button class="side-panel-btn active" id="chatBtn" onclick="window.chatInterface.switchToChat()">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                    </svg>
                </button>
                <button class="side-panel-btn" id="trainBtn" onclick="window.chatInterface.switchToTraining()">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"></path>
                    </svg>
                </button>
            </div>
            
            <div class="chat-container" id="chatContainer">
                <header class="chat-header">
                    <div class="header-content">
                        <h1 class="chat-title">DinaCortex</h1>
                    </div>
                </header>
                
                <div class="chat-messages" id="chatMessages">
                    <div class="welcome-message">
                        <div class="welcome-icon">🤖</div>
                        <h3>Welcome to DinaCortex</h3>
                        <p>I can help you query your database. Try asking questions like:</p>
                        <ul class="example-questions">
                            <li onclick="window.chatInterface.loadExampleQuestion('Show me all customers')">Show me all customers</li>
                            <li onclick="window.chatInterface.loadExampleQuestion('What are the top 10 products by sales?')">What are the top 10 products by sales?</li>
                            <li onclick="window.chatInterface.loadExampleQuestion('How many orders were placed last week?')">How many orders were placed last week?</li>
                        </ul>
                    </div>
                </div>
                  <div class="chat-input-container">
                    <div class="input-wrapper">
                        <textarea 
                            id="chatInput" 
                            placeholder="Ask a question about your data..." 
                            class="chat-input"
                            rows="1"
                        ></textarea>
                        <button id="sendButton" class="send-button">
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <line x1="22" y1="2" x2="11" y2="13"></line>
                                <polygon points="22,2 15,22 11,13 2,9 22,2"></polygon>
                            </svg>
                        </button>
                    </div>
                </div>
            </div>
            
            <div class="training-container" id="trainingContainer" style="display: none;">
                <header class="chat-header">
                    <div class="header-content">
                        <h1 class="chat-title">Training</h1>
                        <p class="chat-subtitle">Train the AI with your SQL examples</p>
                    </div>
                </header>
                  <div class="training-content">
                    <div class="training-form">
                        <!-- Schema Training Section -->
                        <div class="form-group">
                            <label for="schemaNameInput">Schema Name:</label>
                            <input 
                                type="text" 
                                id="schemaNameInput" 
                                placeholder="Enter schema name (e.g., dbo, apex, sales)"
                                class="training-input schema-input"
                                autocomplete="off"
                            />
                            <small class="help-text">Enter the database schema name you want to train the AI with</small>
                        </div>
                        
                        <div class="form-group">
                            <button id="trainSchemaBtn" class="train-schema-btn">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M4 7v10c0 2.21 1.79 4 4 4h8c2.21 0 4-1.79 4-4V7"></path>
                                    <path d="M16 2l-8 0c-1.1 0-2 .9-2 2v2h12V4c0-1.1-.9-2-2-2z"></path>
                                    <line x1="10" y1="11" x2="10" y2="17"></line>
                                    <line x1="14" y1="11" x2="14" y2="17"></line>
                                </svg>
                                Learn Database Schema
                            </button>
                            <small class="help-text">Automatically train the AI with all table and column names from the specified schema</small>                        </div>
                        
                        <hr class="training-divider">
                        
                        <!-- Clear Training Data Section -->
                        <div class="form-group">
                            <button id="clearTrainingBtn" class="clear-training-btn">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <polyline points="3,6 5,6 21,6"></polyline>
                                    <path d="M19,6v14a2,2 0,0,1-2,2H7a2,2 0,0,1-2-2V6m3,0V4a2,2 0,0,1,2-2h4a2,2 0,0,1,2,2v2"></path>
                                    <line x1="10" y1="11" x2="10" y2="17"></line>
                                    <line x1="14" y1="11" x2="14" y2="17"></line>
                                </svg>
                                Clear All Training Data
                            </button>
                            <small class="help-text warning-text">⚠️ This will permanently delete all training data to make space for new training</small>
                        </div>
                        
                        <hr class="training-divider">
                        
                        <!-- Manual Training Form -->
                        <div class="form-group">
                            <label for="trainQuestion">Question:</label>
                            <textarea 
                                id="trainQuestion" 
                                placeholder="Enter a natural language question..."
                                class="training-input"
                                rows="3"
                            ></textarea>
                        </div>
                        
                        <div class="form-group">
                            <label for="trainSQL">SQL Query:</label>
                            <textarea 
                                id="trainSQL" 
                                placeholder="Enter the corresponding SQL query..."
                                class="training-input sql-input"
                                rows="6"
                            ></textarea>
                        </div>
                        
                        <div class="form-group">
                            <label for="trainDocumentation">Documentation (optional):</label>
                            <textarea 
                                id="trainDocumentation" 
                                placeholder="Enter documentation about this query..."
                                class="training-input"
                                rows="4"
                            ></textarea>
                        </div>
                        
                        <button id="trainSubmitBtn" class="train-submit-btn">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.29 1.51 4.04 3 5.5l11 11z"></path>
                            </svg>
                            Add Training Data
                        </button>
                    </div>
                    
                    <div id="trainingMessage" class="training-message" style="display: none;"></div>
                </div>
            </div>
        `;
    }    bindEvents() {
        const input = document.getElementById('chatInput');
        const button = document.getElementById('sendButton');
        const trainButton = document.getElementById('trainSubmitBtn');
        const trainSchemaButton = document.getElementById('trainSchemaBtn');
        const clearTrainingButton = document.getElementById('clearTrainingBtn');

        button.addEventListener('click', () => this.sendMessage());
        trainButton.addEventListener('click', () => this.submitTraining());
        trainSchemaButton.addEventListener('click', () => this.trainSchema());
        clearTrainingButton.addEventListener('click', () => this.clearAllTrainingData());
        
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
        
        // Auto-resize input based on content
        input.addEventListener('input', (e) => {
            const target = e.target;
            target.style.height = 'auto';
            target.style.height = Math.min(target.scrollHeight, 120) + 'px';
        });
    }async sendMessage() {
        const input = document.getElementById('chatInput');
        const message = input.value.trim();
        
        if (!message || this.isLoading) return;

        // Hide welcome message if it exists
        const welcomeMsg = document.querySelector('.welcome-message');
        if (welcomeMsg) welcomeMsg.style.display = 'none';

        // Add user message
        this.addMessage('user', message);
        input.value = '';
        this.setLoading(true);

        try {
            // Step 1: Generate SQL
            const response = await fetch('/api/v0/generate_sql', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question: message })
            });

            const data = await response.json();
            this.handleResponse(data, message);
            
        } catch (error) {
            console.error('Error:', error);
            this.addMessage('assistant', 'Sorry, an error occurred while processing your request.', 'error');
        }

        this.setLoading(false);
    }    handleResponse(data, originalQuestion) {
        if (!data || !data.type) {
            this.addMessage('assistant', 'No response received from server.', 'error');
            return;
        }        switch (data.type) {
            case 'sql':
                this.addMessage('assistant', data.text, 'sql');
                // Pass the entire data object (including id) to runSqlAndShowResults
                this.runSqlAndShowResults(data, originalQuestion);
                break;
            case 'df':
                this.addDataTable(data);
                break;
            case 'plotly_figure':
                this.addVisualization(data);
                break;
            case 'error':
                this.addMessage('assistant', data.error || 'An error occurred', 'error');
                break;
            default:
                this.addMessage('assistant', data.text || 'Unknown response type', 'text');
        }
    }    async runSqlAndShowResults(sqlData, originalQuestion) {
        try {
            // Use the id from the SQL generation response
            const id = sqlData.id;
            
            if (!id) {
                this.addMessage('assistant', 'No ID received from SQL generation.', 'error');
                return;
            }

            // Step 2: Execute SQL using the id
            const runResponse = await fetch(`/api/v0/run_sql?id=${id}`);
            const runData = await runResponse.json();
            
            console.log('Run SQL Response:', runData); // Debug log
              if (runData.type === 'df') {
                // Check if we have data in different possible formats
                if (runData.df || runData.data) {
                    this.addDataTable(runData);
                    
                    // Step 3: Generate visualization using the same id
                    this.generateVisualization(id);
                } else {
                    this.addMessage('assistant', 'Query executed successfully but returned no data.', 'text');
                }
            } else if (runData.type === 'error') {
                this.addMessage('assistant', `Error executing SQL: ${runData.error}`, 'error');
            } else {
                // Handle unexpected response format
                this.addMessage('assistant', `Unexpected response type: ${runData.type}`, 'error');
                console.error('Unexpected run_sql response:', runData);
            }
        } catch (error) {
            console.error('Error running SQL:', error);
            this.addMessage('assistant', 'Error executing the SQL query.', 'error');
        }
    }async generateVisualization(id) {
        try {
            const response = await fetch(`/api/v0/generate_plotly_figure?id=${id}`);
            const data = await response.json();
                  if (data.type === 'plotly_figure') {
                this.addVisualization(data);
            }
        } catch (error) {
            console.error('Error generating visualization:', error);
        }
    }    addMessage(role, content, type = 'text') {
        const messagesContainer = document.getElementById('chatMessages');
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${role}`;
        
        const timestamp = new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        
        // Determine size class based on content length and characteristics
        let sizeClass = '';
        if (type !== 'data' && type !== 'visualization' && type !== 'sql') {
            const contentLength = content.length;
            const wordCount = content.trim().split(/\s+/).length;
            
            // Very short messages (single words, emojis, short responses)
            if (contentLength <= 15 && wordCount <= 2) {
                sizeClass = 'tiny';
            }
            // Short messages (1-2 sentences)
            else if (contentLength <= 80 || wordCount <= 12) {
                sizeClass = 'short';
            }
            // Medium messages (paragraph)
            else if (contentLength <= 250 || wordCount <= 40) {
                sizeClass = 'medium';
            }
            // Long messages
            else if (contentLength <= 500 || wordCount <= 80) {
                sizeClass = 'long';
            }
            // Extra long messages
            else {
                sizeClass = 'extra-long';
            }
        }
        
        messageDiv.innerHTML = `
            <div class="message-avatar">
                ${role === 'user' ? '👤' : '🤖'}
            </div>
            <div class="message-content">
                <div class="message-header">
                    <span class="message-author">${role === 'user' ? 'You' : 'DinaCortex'}</span>
                    <span class="message-time">${timestamp}</span>
                </div>
                <div class="message-text ${type} ${sizeClass}">
                    ${type === 'sql' ? `<pre><code>${this.escapeHtml(content)}</code></pre>` : content}
                </div>
            </div>
        `;

        messagesContainer.appendChild(messageDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }addDataTable(data) {
        const messagesContainer = document.getElementById('chatMessages');
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message assistant';
        
        const timestamp = new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        
        let tableHtml = '';
        let dataArray = null;
        
        // Handle different data formats that might come from the API
        if (data.df) {
            dataArray = typeof data.df === 'string' ? JSON.parse(data.df) : data.df;
        } else if (data.data) {
            dataArray = data.data;
        }
        
        console.log('Data for table:', dataArray); // Debug log
        
        if (dataArray && Array.isArray(dataArray) && dataArray.length > 0) {
            const columns = Object.keys(dataArray[0]);
            tableHtml = `
                <div class="data-table-container">
                    <div class="table-header">
                        <h4>Query Results (${dataArray.length} rows)</h4>
                        <button class="download-btn" onclick="window.chatInterface.downloadCSV('${data.id || ''}')">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                                <polyline points="7,10 12,15 17,10"></polyline>
                                <line x1="12" y1="15" x2="12" y2="3"></line>
                            </svg>
                            CSV
                        </button>
                    </div>
                    <div class="table-wrapper">
                        <table class="data-table">
                            <thead>
                                <tr>
                                    ${columns.map(col => `<th>${this.escapeHtml(col)}</th>`).join('')}
                                </tr>
                            </thead>
                            <tbody>
                                ${dataArray.slice(0, 100).map(row => `
                                    <tr>
                                        ${columns.map(col => `<td>${this.escapeHtml(String(row[col] !== null && row[col] !== undefined ? row[col] : ''))}</td>`).join('')}
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                        ${dataArray.length > 100 ? `<div class="table-footer">Showing first 100 of ${dataArray.length} rows</div>` : ''}
                    </div>
                </div>
            `;
        } else {
            tableHtml = '<div class="no-data">No data returned from query.</div>';
        }
        
        messageDiv.innerHTML = `
            <div class="message-avatar">🤖</div>
            <div class="message-content">
                <div class="message-header">
                    <span class="message-author">DinaCortex</span>
                    <span class="message-time">${timestamp}</span>
                </div>
                <div class="message-text data">
                    ${tableHtml}
                </div>
            </div>
        `;

        messagesContainer.appendChild(messageDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    addVisualization(data) {
        const messagesContainer = document.getElementById('chatMessages');
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message assistant';
        
        const timestamp = new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        const chartId = 'chart-' + Date.now();
        
        messageDiv.innerHTML = `
            <div class="message-avatar">🤖</div>
            <div class="message-content">
                <div class="message-header">
                    <span class="message-author">DinaCortex</span>
                    <span class="message-time">${timestamp}</span>
                </div>
                <div class="message-text visualization">
                    <div class="chart-container">
                        <div id="${chartId}" class="plotly-chart"></div>
                    </div>
                </div>
            </div>
        `;

        messagesContainer.appendChild(messageDiv);
        
        // Render Plotly chart
        if (window.Plotly && data.fig) {
            try {
                const figure = typeof data.fig === 'string' ? JSON.parse(data.fig) : data.fig;
                Plotly.newPlot(chartId, figure.data, figure.layout, {responsive: true});
            } catch (error) {
                console.error('Error rendering chart:', error);
                document.getElementById(chartId).innerHTML = '<div class="chart-error">Error rendering visualization</div>';
            }
        } else {
            document.getElementById(chartId).innerHTML = '<div class="chart-error">Plotly.js not loaded</div>';
        }
        
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    setLoading(loading) {
        this.isLoading = loading;
        const button = document.getElementById('sendButton');
        const input = document.getElementById('chatInput');
        
        if (loading) {
            button.classList.add('loading');
            input.disabled = true;
            this.addTypingIndicator();
        } else {
            button.classList.remove('loading');
            input.disabled = false;
            this.removeTypingIndicator();
        }
    }

    addTypingIndicator() {
        const messagesContainer = document.getElementById('chatMessages');
        const typingDiv = document.createElement('div');
        typingDiv.className = 'message assistant typing-indicator';
        typingDiv.innerHTML = `
            <div class="message-avatar">🤖</div>
            <div class="message-content">
                <div class="typing-dots">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            </div>
        `;
        messagesContainer.appendChild(typingDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    removeTypingIndicator() {
        const typingIndicator = document.querySelector('.typing-indicator');
        if (typingIndicator) {
            typingIndicator.remove();
        }
    }    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }    downloadCSV(id) {
        if (!id) {
            console.error('No ID provided for CSV download');
            return;
        }
          // Open the download endpoint in a new window/tab
        window.open(`/api/v0/download_csv?id=${id}`, '_blank');
    }

    async submitTraining() {
        const question = document.getElementById('trainQuestion').value.trim();
        const sql = document.getElementById('trainSQL').value.trim();
        const documentation = document.getElementById('trainDocumentation').value.trim();
        
        // Validate required fields
        if (!question || !sql) {
            this.showTrainingMessage('Please fill in both question and SQL query fields.', 'error');
            return;
        }
        
        const submitBtn = document.getElementById('trainSubmitBtn');
        const originalContent = submitBtn.innerHTML;
        
        try {
            // Show loading state
            submitBtn.classList.add('loading');
            submitBtn.innerHTML = `
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 12a9 9 0 11-6.219-8.56"></path>
                </svg>
                Training...
            `;
            
            const response = await fetch('/api/v0/train', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    question: question,
                    sql: sql,
                    documentation: documentation || null
                })
            });
            
            const data = await response.json();
            
            if (response.ok) {
                this.showTrainingMessage('Training data added successfully!', 'success');
                this.clearTrainingForm();
            } else {
                this.showTrainingMessage(data.error || 'Failed to add training data.', 'error');
            }
            
        } catch (error) {
            console.error('Training submission error:', error);
            this.showTrainingMessage('Network error. Please try again.', 'error');
        } finally {
            // Reset button state
            submitBtn.classList.remove('loading');
            submitBtn.innerHTML = originalContent;
        }
    }
    
    async trainSchema() {
        const schemaNameInput = document.getElementById('schemaNameInput');
        const trainSchemaBtn = document.getElementById('trainSchemaBtn');
        const originalContent = trainSchemaBtn.innerHTML;
        
        const schemaName = schemaNameInput.value.trim();
        
        // Validate schema name input
        if (!schemaName) {
            this.showTrainingMessage('Please enter a schema name before training.', 'error');
            schemaNameInput.focus();
            return;
        }
        
        try {
            // Show loading state
            trainSchemaBtn.classList.add('loading');
            trainSchemaBtn.innerHTML = `
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 12a9 9 0 11-6.219-8.56"></path>
                </svg>
                Learning Schema...
            `;
            trainSchemaBtn.disabled = true;
            
            const response = await fetch('/api/v0/train_schema', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ schema_name: schemaName })
            });
            
            const data = await response.json();
            
            if (data.type === 'success') {
                this.showTrainingMessage(
                    `✅ ${data.message}\n\nSchema: ${data.schema_name}\nTables learned: ${data.tables_trained}\n\nTable list: ${data.table_list.join(', ')}`, 
                    'success'
                );
                // Clear the input after successful training
                schemaNameInput.value = '';
            } else {
                this.showTrainingMessage(`❌ ${data.error}`, 'error');
            }
            
        } catch (error) {
            console.error('Error training schema:', error);
            this.showTrainingMessage('❌ Failed to train schema. Please try again.', 'error');
        } finally {
            // Restore button state
            trainSchemaBtn.classList.remove('loading');
            trainSchemaBtn.innerHTML = originalContent;
            trainSchemaBtn.disabled = false;        }
    }
    
    async clearAllTrainingData() {
        const clearTrainingBtn = document.getElementById('clearTrainingBtn');
        const originalContent = clearTrainingBtn.innerHTML;
        
        // Show confirmation dialog
        const confirmed = confirm(
            '⚠️ Are you sure you want to clear ALL training data?\n\n' +
            'This will permanently delete:\n' +
            '• All question-SQL pairs\n' +
            '• All DDL schema definitions\n' +
            '• All documentation\n' +
            '• All learned patterns\n\n' +
            'This action cannot be undone!'
        );
        
        if (!confirmed) {
            return;
        }
        
        try {
            // Show loading state
            clearTrainingBtn.classList.add('loading');
            clearTrainingBtn.innerHTML = `
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 12a9 9 0 11-6.219-8.56"></path>
                </svg>
                Clearing Training Data...
            `;
            clearTrainingBtn.disabled = true;
            
            const response = await fetch('/api/v0/clear_all_training_data', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            
            const data = await response.json();
            
            if (data.type === 'success') {
                this.showTrainingMessage(
                    `✅ ${data.message}\n\nCleared ${data.cleared_count} training entries.\n\nYou can now train with new data!`, 
                    'success'
                );
            } else if (data.type === 'warning') {
                this.showTrainingMessage(`⚠️ ${data.message}`, 'warning');
            } else {
                this.showTrainingMessage(`❌ ${data.error}`, 'error');
            }
            
        } catch (error) {
            console.error('Error clearing training data:', error);
            this.showTrainingMessage('❌ Failed to clear training data. Please try again.', 'error');
        } finally {
            // Restore button state
            clearTrainingBtn.classList.remove('loading');
            clearTrainingBtn.innerHTML = originalContent;
            clearTrainingBtn.disabled = false;
        }
    }
    
    // ...existing code...
    switchToChat() {
        document.getElementById('chatContainer').style.display = 'flex';
        document.getElementById('trainingContainer').style.display = 'none';
        document.getElementById('chatBtn').classList.add('active');
        document.getElementById('trainBtn').classList.remove('active');
    }

    switchToTraining() {
        document.getElementById('chatContainer').style.display = 'none';
        document.getElementById('trainingContainer').style.display = 'flex';
        document.getElementById('trainBtn').classList.add('active');
        document.getElementById('chatBtn').classList.remove('active');
    }

    loadExampleQuestion(question) {
        document.getElementById('chatInput').value = question;
        document.getElementById('sendButton').click();
    }
}

// Initialize the chat when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.chatInterface = new ChatInterface();
});