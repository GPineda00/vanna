# Training Vanna with Apex Schema Only

This guide explains how to train the Vanna SQL Assistant with only the Apex database schema, ensuring all SQL queries generated will target only the relevant tables.

## Why Limit Training to Apex Schema

Training the LLM with only the Apex schema offers several benefits:

1. **Focused Responses**: The model will only generate SQL for tables it knows about
2. **Improved Accuracy**: Reduced confusion from unrelated schemas in the database
3. **Better Performance**: Smaller training dataset means faster responses
4. **Data Privacy**: Only exposes the specific schema needed for the application

## How to Use the Training Script

### Step 1: Clear Existing Training Data (Optional)

If you want to start with a clean slate, run:

```bash
python train_apex_only.py --clear
```

This removes any existing training data from the Vanna system.

### Step 2: Train on Apex Schema

To load just the Apex schema (tables, columns, and relationships):

```bash
python train_apex_only.py
```

This will:
- Extract all tables from the Apex schema
- Create DDL statements for each table
- Extract and add primary and foreign key relationships
- Add documentation about the schema structure

### Step 3: Add Example Queries (Optional but Recommended)

To create a template for example queries:

```bash
python train_apex_only.py --create-examples
```

This creates a file called `apex_sample_queries.json` that you can edit with Apex-specific example queries.

After editing the examples, load them into Vanna:

```bash
python train_apex_only.py --examples
```

Or you can specify a custom examples file:

```bash
python train_apex_only.py --examples --example-file custom_apex_examples.json
```

### Step 4: Run All Steps Together

To perform a complete training in one command:

```bash
python train_apex_only.py --clear --examples
```

This clears existing data, loads the Apex schema, and adds the example queries.

## Verifying the Training

After training, you can verify the model works correctly by:

1. Running the web application
2. Asking a question about Apex data
3. Confirming the generated SQL only references Apex schema tables

## Customizing Further

You can modify the `train_apex_only.py` script to:

- Add specific documentation about the purpose of Apex tables
- Include table descriptions from database comments
- Add more complex relationship documentation
- Filter to only specific tables within Apex
